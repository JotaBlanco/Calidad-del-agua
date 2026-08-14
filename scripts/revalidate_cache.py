#!/usr/bin/env python3
"""Re-valida el cache fino existente con la regla escalonada de geo_validate.

Aplica la nueva regla (dentro del término / banda de gracia de 2 km / exención
de infraestructura / colapso al centroide) a todos los hits ya cacheados que
están etiquetados como "geocodificado*", y desglosa cuántos deberían
degradarse a "centroide" por proveedor y por nivel de limpieza.

Por defecto NO escribe nada. Con --apply degrada en el cache los hits que
suspenden, cambiando su source a "centroide" y su coordenada al centroide
municipal (que es lo que honestamente representan).

Uso:
    python scripts/revalidate_cache.py                       # informe
    python scripts/revalidate_cache.py --apply               # degrada en el cache
    python scripts/revalidate_cache.py --examples 15         # peores casos
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo_validate import (  # noqa: E402
    MunicipalTerms, VERDICTS_ACCEPT, infra_terms_in, validate_candidate,
)

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "processed" / "geocoded_puntos_cache.json"
MUNI_CSV = ROOT / "data" / "processed" / "geocoded_municipalities.csv"

VERDICT_ORDER = ["inside", "grace", "infra_exempt", "no_polygon",
                 "centroid_collapse", "outside", "too_far"]


def parse_source(src: str) -> tuple[str, str]:
    """'geocodificado (photon/B)' -> ('photon','B'); 'geocodificado' -> ('(sin etiqueta)','(sin etiqueta)')."""
    m = re.match(r"geocodificado\s*\(([^/)]+)(?:/([^)]+))?\)", src)
    if not m:
        return ("(sin etiqueta)", "(sin etiqueta)")
    return (m.group(1).strip(), (m.group(2) or "(sin nivel)").strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--muni-dir", default=None)
    ap.add_argument("--examples", type=int, default=10)
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args()

    terms = MunicipalTerms(Path(args.muni_dir) if args.muni_dir else None)
    cache = json.loads(CACHE.read_text(encoding="utf-8"))

    mdf = pd.read_csv(MUNI_CSV, dtype={"provincia_code": str, "municipio_code": str})
    centroids = {str(r.municipio_code).zfill(5):
                 (float(r.lat_municipio), float(r.lon_municipio))
                 for r in mdf.itertuples(index=False)}

    # ordenar por provincia para cargar cada geojson una sola vez
    items = []
    for key, val in cache.items():
        if not str(val.get("source", "")).startswith("geocodificado"):
            continue
        parts = key.split("_", 2)
        if len(parts) != 3:
            continue
        items.append((parts[0].zfill(2), parts[1].zfill(5), parts[2], key, val))
    items.sort(key=lambda x: x[0])
    print(f"hits finos a re-validar: {len(items):,}\n")

    verdicts = collections.Counter()
    by_provider = collections.defaultdict(collections.Counter)
    by_level = collections.defaultdict(collections.Counter)
    prov_demoted = collections.Counter()
    prov_total = collections.Counter()
    skipped = collections.Counter()
    rows, worst = [], []

    for prov, mun, punto, key, val in items:
        centroid = centroids.get(mun)
        if centroid is None:
            skipped["sin centroide municipal"] += 1
            continue
        ok, verdict, dist = validate_candidate(
            val["lat"], val["lon"], mun, centroid, punto, terms)
        provider, level = parse_source(val["source"])
        verdicts[verdict] += 1
        by_provider[provider][verdict] += 1
        by_level[level][verdict] += 1
        prov_total[prov] += 1
        if not ok:
            prov_demoted[prov] += 1
            worst.append((dist, verdict, punto, mun, provider, level))
        rows.append({
            "key": key, "prov_code": prov, "mun_code": mun, "punto": punto,
            "provider": provider, "level": level, "verdict": verdict,
            "accept": int(ok), "dist_km": round(dist, 3),
            "lat": val["lat"], "lon": val["lon"],
            "infra_terms": "|".join(sorted(set(infra_terms_in(punto)))),
        })

    total = sum(verdicts.values())
    demoted = sum(v for k, v in verdicts.items() if k not in VERDICTS_ACCEPT)
    accepted = total - demoted

    print("=" * 72)
    print("RE-VALIDACION NACIONAL DEL CACHE FINO")
    print("=" * 72)
    print(f"{'veredicto':22} {'puntos':>8}  {'%':>6}")
    print("-" * 72)
    for v in VERDICT_ORDER:
        if verdicts[v]:
            mark = "  ACEPTA" if v in VERDICTS_ACCEPT else "  DEGRADA"
            print(f"{v:22} {verdicts[v]:8,}  {100*verdicts[v]/total:5.1f}%{mark}")
    print("-" * 72)
    print(f"{'ACEPTADOS':22} {accepted:8,}  {100*accepted/total:5.1f}%")
    print(f"{'A DEGRADAR':22} {demoted:8,}  {100*demoted/total:5.1f}%")
    if skipped:
        print(f"\nomitidos: {dict(skipped)}")
    if terms.missing_provinces:
        print(f"!! provincias sin polígono: {sorted(terms.missing_provinces)}")

    # --- desglose por proveedor -------------------------------------------
    for title, table in (("PROVEEDOR", by_provider), ("NIVEL DE LIMPIEZA", by_level)):
        print("\n" + "=" * 72)
        print(f"A DEGRADAR POR {title}")
        print("=" * 72)
        hdr = f"{title.lower():16} {'total':>7} {'degradar':>9} {'%':>6} " \
              f"{'colapso':>8} {'fuera':>7} {'>50km':>6}"
        print(hdr)
        print("-" * 72)
        for k in sorted(table, key=lambda x: -sum(table[x].values())):
            c = table[k]
            t = sum(c.values())
            d = sum(v for vv, v in c.items() if vv not in VERDICTS_ACCEPT)
            print(f"{k:16} {t:7,} {d:9,} {100*d/t:5.1f}% "
                  f"{c['centroid_collapse']:8,} {c['outside']:7,} {c['too_far']:6,}")

    # --- provincias con más degradaciones ---------------------------------
    print("\n" + "=" * 72)
    print("PROVINCIAS CON MAYOR TASA DE DEGRADACION (min. 100 hits)")
    print("=" * 72)
    ranked = [(prov_demoted[p] / prov_total[p], p, prov_demoted[p], prov_total[p])
              for p in prov_total if prov_total[p] >= 100]
    for rate, p, d, t in sorted(ranked, reverse=True)[:12]:
        print(f"  provincia {p}: {d:5,}/{t:6,} = {100*rate:5.1f}%")

    if args.examples and worst:
        print("\n" + "=" * 72)
        print(f"PEORES CASOS ({args.examples})")
        print("=" * 72)
        for d, v, punto, mun, prov, lvl in sorted(worst, reverse=True)[:args.examples]:
            print(f"  {d:8.1f} km {v:18} {prov}/{lvl:12} mun {mun} | {punto[:46]}")

    if args.out_report and rows:
        with open(args.out_report, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\ninforme -> {args.out_report}")

    # --- aplicar -----------------------------------------------------------
    if args.apply and demoted:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = CACHE.with_suffix(f".json.bak_{stamp}")
        shutil.copy2(CACHE, backup)
        print(f"\nbackup -> {backup.name}")
        n = 0
        for r in rows:
            if r["accept"]:
                continue
            c = centroids[r["mun_code"]]
            cache[r["key"]] = {"lat": c[0], "lon": c[1], "source": "centroide"}
            n += 1
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        print(f"degradados a 'centroide': {n:,}")
    elif not args.apply:
        print("\n(informe solamente — usa --apply para degradar en el cache)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
