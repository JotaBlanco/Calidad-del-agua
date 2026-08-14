#!/usr/bin/env python3
"""Audita (y opcionalmente reconstruye) data/processed/geocoded_municipalities.csv.

Los centroides actuales vienen de Nominatim sobre "{municipio}, {provincia},
España", con los fallos típicos de eso: municipios homónimos resueltos a la
provincia equivocada, y municipios que no resolvieron.

Este script mide:
  1. cuántos centroides caen FUERA de su propio término municipal,
  2. cuántos están lejos del punto representativo del propio polígono,
  3. cuántos municipios faltan respecto al catálogo de polígonos.

Y con --apply los reconstruye. Prioridad de fuentes (la mejor primero):
  1. NÚCLEO PRINCIPAL del nomenclátor (GeoNames): entidad PPLA*/PPLC del
     municipio, o el PPL homónimo más poblado, validado dentro del polígono.
     Es el mejor ancla para puntos de muestreo de agua: las fuentes y los
     grifos están en el pueblo, no en medio del campo.
  2. geo_point_2d del polígono (georef-spain-municipio), si cae dentro.
  3. PPL cualquiera del municipio más cercano a geo_point_2d.
  4. geo_point_2d aunque caiga fuera (último recurso, se marca).

Uso:
    python scripts/audit_centroids.py                  # sólo auditoría
    python scripts/audit_centroids.py --apply          # reconstruye (con backup)
"""
from __future__ import annotations

import argparse
import collections
import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo_validate import (  # noqa: E402
    MunicipalTerms, _in_ring, dist_to_boundary_km, haversine_km, in_poly,
)

ROOT = Path(__file__).resolve().parent.parent
MUNI_CSV = ROOT / "data" / "processed" / "geocoded_municipalities.csv"
REF_CSV = ROOT / "data" / "processed" / "municipal_reference_points.csv"
GAZ = ROOT / "data" / "geo" / "gazetteer" / "gazetteer_places.csv"

CAPITAL_CODES = ("PPLC", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5")


def load_gazetteer_by_mun() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = collections.defaultdict(list)
    with GAZ.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["feature_class"] != "P":
                continue
            mun = r["admin3"] or ""
            if len(mun) != 5 or not mun.isdigit():
                continue
            out[mun].append({
                "name": r["name"], "lat": float(r["lat"]), "lon": float(r["lon"]),
                "fcode": r["feature_code"], "pop": int(r["population"] or 0),
            })
    return out


def norm(s: str) -> str:
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").upper().strip()
    # "NAVA (LA)" -> "LA NAVA"
    m = re.match(r"^(.*?)\s*\((EL|LA|LOS|LAS|L|A|O|AS|OS|ES|SA|S)\)$", s)
    if m:
        s = f"{m.group(2)} {m.group(1)}".strip()
    return re.sub(r"[^A-Z0-9 ]", " ", s).strip()


def representative_point_inside(polys) -> tuple[float, float] | None:
    """Punto GARANTIZADO dentro del polígono (lat, lon), en Python puro.

    geo_point_2d es el centroide geométrico y en municipios cóncavos o
    multiparte puede caer fuera del término. Aquí se toma el anillo exterior
    más grande, y sobre una línea horizontal a su latitud media se buscan los
    cruces con el anillo: el centro del intervalo interior más ancho está
    siempre dentro. Es el equivalente ligero de un "representative point".
    """
    if not polys:
        return None
    # anillo exterior con más vértices = la parte principal del municipio
    ring = max((p[0] for p in polys), key=len)
    ys = [c[1] for c in ring]
    y = (min(ys) + max(ys)) / 2.0
    xs = []
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > y) != (y2 > y):
            xs.append(x1 + (x2 - x1) * (y - y1) / (y2 - y1))
    xs.sort()
    best_w, best_x = -1.0, None
    for i in range(0, len(xs) - 1, 2):
        w = xs[i + 1] - xs[i]
        if w > best_w:
            best_w, best_x = w, (xs[i] + xs[i + 1]) / 2.0
    if best_x is None:
        return None
    if not in_poly(best_x, y, polys):
        return None
    return (y, best_x)


def pick_anchor(mun: str, term: dict, ents: list[dict], terms: MunicipalTerms):
    """Devuelve (lat, lon, fuente)."""
    mname = norm(term.get("name") or "")
    inside = lambda e: terms.contains(mun, e["lon"], e["lat"])  # noqa: E731

    # 1a. capital administrativa dentro del término
    caps = [e for e in ents if e["fcode"] in CAPITAL_CODES and inside(e)]
    if caps:
        best = max(caps, key=lambda e: (e["fcode"] == "PPLA3", e["pop"]))
        return best["lat"], best["lon"], "nucleo_capital_geonames"

    # 1b. PPL homónimo del municipio, dentro del término
    same = [e for e in ents if norm(e["name"]) == mname and inside(e)]
    if same:
        best = max(same, key=lambda e: e["pop"])
        return best["lat"], best["lon"], "nucleo_homonimo_geonames"

    # 2. geo_point_2d del polígono si cae dentro
    gp = term.get("geo_point")
    if gp and terms.contains(mun, gp[1], gp[0]):
        return gp[0], gp[1], "geo_point_2d_poligono"

    # 3. PPL cualquiera dentro del término, el más poblado
    ins = [e for e in ents if inside(e)]
    if ins:
        best = max(ins, key=lambda e: e["pop"])
        return best["lat"], best["lon"], "nucleo_cualquiera_geonames"

    # 4. punto representativo garantizado dentro del polígono
    rp = representative_point_inside(term["polys"])
    if rp:
        return rp[0], rp[1], "punto_representativo_poligono"

    # 5. último recurso
    if gp:
        return gp[0], gp[1], "geo_point_2d_FUERA_del_poligono"
    return None


def write_reference_points(all_terms: dict[str, dict],
                           ents_by_mun: dict[str, list[dict]],
                           old_centroids: dict[str, tuple],
                           rows: list[dict]) -> int:
    """Escribe municipal_reference_points.csv: todos los puntos conocidos "a
    nivel de municipio" de cada término.

    Los usa el filtro de colapso de geo_validate: si un candidato cae a menos
    de 150 m de CUALQUIERA de ellos, el geocodificador devolvió el municipio y
    no el punto, así que no es un hit fino.

    Se acumula de forma idempotente con lo que ya hubiera en el fichero, para
    no perder el centroide histórico de Nominatim (que es justo el punto que
    Photon devuelve al no resolver una cadena) cuando se re-ejecuta el script.
    """
    seen: set[tuple[str, float, float]] = set()
    out: list[dict] = []

    def add(code: str, lat: float, lon: float, kind: str) -> None:
        k = (code, round(lat, 4), round(lon, 4))
        if k in seen:
            return
        seen.add(k)
        out.append({"municipio_code": code, "lat": round(lat, 7),
                    "lon": round(lon, 7), "kind": kind})

    # 1. lo ya acumulado en ejecuciones anteriores
    if REF_CSV.exists():
        with REF_CSV.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                add(str(r["municipio_code"]).zfill(5), float(r["lat"]),
                    float(r["lon"]), r.get("kind") or "previo")
    # 2. centroide histórico (Nominatim) que estamos reemplazando
    for code, (lat, lon, _n) in old_centroids.items():
        add(code, lat, lon, "centroide_nominatim_historico")
    # 3. ancla nueva
    for r in rows:
        add(str(r["municipio_code"]).zfill(5), r["lat_municipio"],
            r["lon_municipio"], "ancla_actual")
    # 4. geo_point_2d del polígono
    for code, t in all_terms.items():
        gp = t.get("geo_point")
        if gp:
            add(code, gp[0], gp[1], "geo_point_2d")
    # 5. capitales y núcleo más poblado del nomenclátor
    for code, ents in ents_by_mun.items():
        if code not in all_terms:
            continue
        for e in ents:
            if e["fcode"] in CAPITAL_CODES:
                add(code, e["lat"], e["lon"], "capital_geonames")
        if ents:
            top = max(ents, key=lambda e: e["pop"])
            if top["pop"] > 0:
                add(code, top["lat"], top["lon"], "nucleo_mas_poblado")

    with REF_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["municipio_code", "lat", "lon", "kind"])
        w.writeheader()
        w.writerows(out)
    return len(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--muni-dir", default=None)
    ap.add_argument("--examples", type=int, default=12)
    args = ap.parse_args()

    terms = MunicipalTerms(Path(args.muni_dir) if args.muni_dir else None)
    df = pd.read_csv(MUNI_CSV, dtype={"provincia_code": str, "municipio_code": str})
    print(f"centroides actuales en {MUNI_CSV.name}: {len(df):,}")

    # Catálogo completo de polígonos. Se excluye la pseudo-provincia "53" del
    # dataset georef-spain-municipio: no son municipios sino territorios
    # no municipales compartidos (Bardenas Reales, Sierra de Aralar,
    # Parzonería de Entzia, mancomunidades, ledanías...). Al excluirlos quedan
    # exactamente 8.131 municipios, que es el censo del proyecto.
    all_terms: dict[str, dict] = {}
    n53 = 0
    for i in range(1, 53):
        for code, t in terms.province(f"{i:02d}").items():
            if code.startswith("53"):
                n53 += 1
                continue
            all_terms[code] = t
    print(f"términos municipales con polígono: {len(all_terms):,} "
          f"(excluidos {n53} territorios no municipales con código 53xxx)")
    if terms.missing_provinces:
        print(f"!! provincias sin fichero: {sorted(terms.missing_provinces)}")

    # ---------------- auditoría ----------------
    stats = collections.Counter()
    outside_list, farlist = [], []
    cur: dict[str, tuple[float, float, str]] = {}
    for r in df.itertuples(index=False):
        mun = str(r.municipio_code).zfill(5)
        lat, lon = float(r.lat_municipio), float(r.lon_municipio)
        cur[mun] = (lat, lon, r.municipio)
        t = all_terms.get(mun)
        if not t:
            stats["sin polígono para ese código"] += 1
            continue
        if terms.contains(mun, lon, lat):
            stats["DENTRO de su término"] += 1
        else:
            d = dist_to_boundary_km(lon, lat, t["polys"])
            outside_list.append((d, mun, r.municipio, t["name"]))
            if d <= 1:
                stats["fuera, <=1 km del borde"] += 1
            elif d <= 5:
                stats["fuera, 1-5 km"] += 1
            else:
                stats["fuera, >5 km (ERROR CLARO)"] += 1
        gp = t.get("geo_point")
        if gp:
            dd = haversine_km(lat, lon, gp[0], gp[1])
            if dd > 10:
                farlist.append((dd, mun, r.municipio))

    total = len(df)
    print("\n" + "=" * 68)
    print("AUDITORIA DE LOS CENTROIDES ACTUALES (Nominatim)")
    print("=" * 68)
    for k in ["DENTRO de su término", "fuera, <=1 km del borde", "fuera, 1-5 km",
              "fuera, >5 km (ERROR CLARO)", "sin polígono para ese código"]:
        if stats[k]:
            print(f"  {k:32} {stats[k]:6,}  {100*stats[k]/total:5.1f}%")
    n_out = sum(stats[k] for k in stats if k.startswith("fuera"))
    print(f"\n  fuera de su término, en total:     {n_out:6,}  {100*n_out/total:5.1f}%")
    print(f"  a más de 10 km del punto del polígono: {len(farlist):,}")

    missing = set(all_terms) - set(cur)
    print(f"  municipios con polígono pero SIN centroide: {len(missing):,}")
    if missing and args.examples:
        for m in sorted(missing)[: args.examples]:
            print(f"      falta {m} {all_terms[m]['name']}")

    if outside_list and args.examples:
        print(f"\npeores centroides (más lejos de su propio término):")
        for d, mun, name, pname in sorted(outside_list, reverse=True)[: args.examples]:
            print(f"  {d:8.1f} km  {mun}  csv='{name}'  polígono='{pname}'")

    # ---------------- reconstrucción ----------------
    print("\n" + "=" * 68)
    print("RECONSTRUCCION PROPUESTA (núcleo principal del nomenclátor)")
    print("=" * 68)
    ents_by_mun = load_gazetteer_by_mun()
    src_counts = collections.Counter()
    moved = []
    rows = []
    for mun in sorted(all_terms):
        t = all_terms[mun]
        got = pick_anchor(mun, t, ents_by_mun.get(mun, []), terms)
        if got is None:
            src_counts["SIN ANCLA"] += 1
            continue
        lat, lon, src = got
        src_counts[src] += 1
        old = cur.get(mun)
        if old:
            d = haversine_km(old[0], old[1], lat, lon)
            moved.append((d, mun, t["name"], src))
        rows.append({
            "provincia_code": str(int(mun[:2])),
            "municipio_code": str(int(mun)),
            # se conserva el nombre que ya había en el CSV para no romper
            # ningún join por nombre; los municipios nuevos toman el del polígono
            "municipio": old[2] if old else t["name"],
            "lat_municipio": round(lat, 7),
            "lon_municipio": round(lon, 7),
            "source": src,
        })
    print(f"  municipios con ancla nueva: {len(rows):,}")
    for k, v in src_counts.most_common():
        print(f"    {k:38} {v:6,}")
    inside_new = sum(1 for r in rows
                     if terms.contains(str(r['municipio_code']).zfill(5),
                                       r["lon_municipio"], r["lat_municipio"]))
    print(f"\n  anclas nuevas DENTRO de su término: {inside_new:,}/{len(rows):,} "
          f"= {100*inside_new/len(rows):.2f}%   (antes: "
          f"{100*stats['DENTRO de su término']/total:.2f}%)")
    if moved:
        ds = sorted(d for d, *_ in moved)
        print(f"  desplazamiento vs centroide viejo: mediana "
              f"{ds[len(ds)//2]:.2f} km, p90 {ds[int(.9*len(ds))]:.2f} km, "
              f"max {ds[-1]:.1f} km")
        print(f"  municipios que se mueven >5 km: "
              f"{sum(1 for d in ds if d > 5):,}")
        if args.examples:
            print("\n  mayores correcciones:")
            for d, mun, name, src in sorted(moved, reverse=True)[: args.examples]:
                print(f"    {d:8.1f} km  {mun} {name[:30]:30} <- {src}")

    if args.apply:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = MUNI_CSV.with_suffix(f".csv.bak_{stamp}")
        shutil.copy2(MUNI_CSV, backup)
        print(f"\nbackup -> {backup.name}")
        n_ref = write_reference_points(all_terms, ents_by_mun, cur, rows)
        print(f"escrito {REF_CSV.name}: {n_ref:,} puntos de referencia "
              f"a nivel de municipio (para el filtro de colapso)")
        out = pd.DataFrame(rows)
        out.to_csv(MUNI_CSV, index=False)
        print(f"escrito {MUNI_CSV.name}: {len(out):,} municipios "
              f"(antes {total:,})")
    else:
        print("\n(auditoría solamente — usa --apply para reconstruir el CSV)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
