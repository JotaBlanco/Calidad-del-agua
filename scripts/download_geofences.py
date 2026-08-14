#!/usr/bin/env python3
"""
download_geofences.py — Descarga los límites administrativos de España
(comunidades autónomas, provincias y municipios) y los deja en local.

Fuente: dataset `georef-spain-*` de Opendatasoft, derivado de las Líneas Límite
Municipales del IGN / Registro Central de Cartografía.
Licencia: reutilización libre con atribución al IGN.

Salida:

    data/geo/comunidades.geojson          full-res, 20 features
    data/geo/provincias.geojson           full-res, 53 features
    data/geo/municipios/{NN}.geojson      full-res, una por provincia (INE 2 díg.)
    data/geo/index.json                   metadatos (fuente, fecha, recuentos)

    docs/dashboard/geo/...                mismo árbol, geometrías simplificadas
                                          para servir al navegador

Ojo con la ubicación de la copia del dashboard: va en docs/dashboard/geo/ y NO
en docs/dashboard/data/geo/. Todo lo que cuelga de docs/dashboard/data/ lo genera
y commitea el workflow nocturno (ver docs/dashboard/data/README.md), y su job
`publish` hace `git add docs/dashboard/data`, así que estos ficheros escritos a
mano acabarían mezclados en commits de datos generados.

Las geometrías vienen en WGS84 (EPSG:4326), el mismo sistema que las coordenadas
de los puntos de muestreo, así que no hay reproyección en ningún punto.

Uso:
    python scripts/download_geofences.py              # descarga lo que falte
    python scripts/download_geofences.py --force      # vuelve a descargar todo
    python scripts/download_geofences.py --only 09,15 # solo esas provincias
    python scripts/download_geofences.py --simplify-only  # rehace docs/ sin bajar nada
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

ODS_BASE = "https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets"

DATASET_CA = "georef-spain-comunidad-autonoma"
DATASET_PROV = "georef-spain-provincia"
DATASET_MUN = "georef-spain-municipio"

SOURCE_ATTRIBUTION = (
    "Opendatasoft georef-spain-* (derivado de las Líneas Límite Municipales "
    "del IGN / Registro Central de Cartografía). Reutilización libre con atribución."
)

REPO_ROOT = Path(__file__).resolve().parent.parent
GEO_DIR = REPO_ROOT / "data" / "geo"
DASH_GEO_DIR = REPO_ROOT / "docs" / "dashboard" / "geo"

REQUEST_DELAY = 0.5  # cortesía con la API pública
MAX_RETRIES = 4
TIMEOUT = 180

# Tolerancias de simplificación en grados, aproximadamente:
#   0.010° ≈ 1.100 m   0.003° ≈ 330 m   0.0008° ≈ 90 m
# Las CCAA se ven a zoom nacional, los municipios a zoom de calle.
TOLERANCE_CA = 0.010
TOLERANCE_PROV = 0.005
TOLERANCE_MUN = 0.0008


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None) -> requests.Response:
    """GET con reintentos y backoff exponencial."""
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            time.sleep(REQUEST_DELAY)
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"    429 recibido, esperando {wait}s…")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except (requests.RequestException, OSError) as err:
            last_err = err
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"    error ({err}), reintento en {wait}s…")
                time.sleep(wait)
    raise RuntimeError(f"fallo tras {MAX_RETRIES} intentos: {url} ({last_err})")


def export_geojson(dataset: str, fields: list[str], where: str | None = None) -> dict:
    """Exporta un dataset ODS completo (o filtrado) como GeoJSON."""
    params = {"select": ",".join(fields)}
    if where:
        params["where"] = where
    resp = _get(f"{ODS_BASE}/{dataset}/exports/geojson", params=params)
    return resp.json()


def list_provincias() -> list[dict]:
    """Devuelve [{prov_code, prov_name, acom_code, acom_name}, …] ordenado por código."""
    resp = _get(
        f"{ODS_BASE}/{DATASET_PROV}/records",
        params={
            "select": "prov_code,prov_name,acom_code,acom_name",
            "limit": 100,
            "order_by": "prov_code",
        },
    )
    return resp.json().get("results", [])


# ---------------------------------------------------------------------------
# Simplificación (Douglas-Peucker en Python puro, sin shapely)
# ---------------------------------------------------------------------------

def _perp_dist(p: list, a: list, b: list, kx: float) -> float:
    """Distancia perpendicular de p al segmento a-b, con la longitud escalada."""
    px, py = p[0] * kx, p[1]
    ax, ay = a[0] * kx, a[1]
    bx, by = b[0] * kx, b[1]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _douglas_peucker(pts: list, tol: float, kx: float) -> list:
    """Simplifica una polilínea. Iterativo, para no reventar la pila en anillos grandes."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        max_d, idx = -1.0, first
        for i in range(first + 1, last):
            d = _perp_dist(pts[i], pts[first], pts[last], kx)
            if d > max_d:
                max_d, idx = d, i
        if max_d > tol:
            keep[idx] = True
            stack.append((first, idx))
            stack.append((idx, last))
    return [p for p, k in zip(pts, keep) if k]


def _simplify_ring(ring: list, tol: float) -> list | None:
    """Simplifica un anillo cerrado. Devuelve None si degenera."""
    if len(ring) < 4:
        return ring
    mean_lat = sum(p[1] for p in ring) / len(ring)
    kx = math.cos(math.radians(mean_lat)) or 1.0
    # El anillo está cerrado: simplifica sin el punto duplicado y vuelve a cerrar.
    simplified = _douglas_peucker(ring[:-1], tol, kx)
    if len(simplified) < 3:
        return None  # ya no es un polígono
    return simplified + [simplified[0]]


def simplify_geometry(geom: dict, tol: float) -> dict | None:
    """Simplifica un Polygon o MultiPolygon. Descarta anillos degenerados."""
    gtype = geom.get("type")
    if gtype == "Polygon":
        rings = [_simplify_ring(r, tol) for r in geom["coordinates"]]
        rings = [r for r in rings if r]
        if not rings:
            return None
        return {"type": "Polygon", "coordinates": rings}
    if gtype == "MultiPolygon":
        polys = []
        for poly in geom["coordinates"]:
            rings = [_simplify_ring(r, tol) for r in poly]
            rings = [r for r in rings if r]
            if rings:
                polys.append(rings)
        if not polys:
            return None
        return {"type": "MultiPolygon", "coordinates": polys}
    return geom


def count_vertices(geom: dict) -> int:
    if not geom:
        return 0
    if geom.get("type") == "Polygon":
        return sum(len(r) for r in geom["coordinates"])
    if geom.get("type") == "MultiPolygon":
        return sum(len(r) for p in geom["coordinates"] for r in p)
    return 0


def _round_coords(obj, nd: int):
    """Redondea coordenadas in-place para recortar bytes inútiles."""
    if isinstance(obj, list):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(v), nd) for v in obj]
        return [_round_coords(o, nd) for o in obj]
    return obj


def simplify_feature_collection(fc: dict, tol: float, precision: int = 5) -> dict:
    """Devuelve una copia simplificada de la FeatureCollection."""
    out = []
    for feat in fc.get("features", []):
        geom = simplify_geometry(feat.get("geometry") or {}, tol)
        if not geom:
            continue
        geom["coordinates"] = _round_coords(geom["coordinates"], precision)
        out.append({
            "type": "Feature",
            "properties": feat.get("properties", {}),
            "geometry": geom,
        })
    return {"type": "FeatureCollection", "features": out}


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------

def write_json(path: Path, data: dict, compact: bool = True) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        if compact:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, fh, ensure_ascii=False, indent=1)
    return path.stat().st_size


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


# ---------------------------------------------------------------------------
# Pasos
# ---------------------------------------------------------------------------

def fetch_comunidades(force: bool) -> dict:
    path = GEO_DIR / "comunidades.geojson"
    if path.exists() and not force:
        print("comunidades.geojson ya existe, se reutiliza")
        return json.loads(path.read_text(encoding="utf-8"))
    print("Descargando comunidades autónomas…")
    fc = export_geojson(DATASET_CA, ["acom_code", "acom_name", "acom_iso3166_code"])
    size = write_json(path, fc)
    print(f"  {len(fc['features'])} features, {_fmt_size(size)}")
    return fc


def fetch_provincias(force: bool) -> dict:
    path = GEO_DIR / "provincias.geojson"
    if path.exists() and not force:
        print("provincias.geojson ya existe, se reutiliza")
        return json.loads(path.read_text(encoding="utf-8"))
    print("Descargando provincias…")
    fc = export_geojson(DATASET_PROV, ["prov_code", "prov_name", "acom_code", "acom_name"])
    size = write_json(path, fc)
    print(f"  {len(fc['features'])} features, {_fmt_size(size)}")
    return fc


def fetch_municipios(provincias: list[dict], force: bool, only: set[str] | None) -> dict:
    """Descarga los municipios provincia a provincia. Devuelve {prov_code: n_features}."""
    out_dir = GEO_DIR / "municipios"
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    targets = [p for p in provincias if not only or p["prov_code"] in only]
    print(f"\nDescargando municipios de {len(targets)} provincias…")

    for i, prov in enumerate(targets, 1):
        code = prov["prov_code"]
        name = prov["prov_name"]
        path = out_dir / f"{code}.geojson"

        if path.exists() and not force:
            fc = json.loads(path.read_text(encoding="utf-8"))
            counts[code] = len(fc["features"])
            print(f"  [{i:2}/{len(targets)}] {code} {name:24} — ya existe ({counts[code]} municipios)")
            continue

        fc = export_geojson(
            DATASET_MUN,
            ["mun_code", "mun_name", "mun_name_local", "prov_code", "prov_name",
             "acom_code", "acom_name", "geo_point_2d"],
            where=f'prov_code="{code}"',
        )
        n = len(fc["features"])
        counts[code] = n
        size = write_json(path, fc)
        print(f"  [{i:2}/{len(targets)}] {code} {name:24} — {n:4} municipios, {_fmt_size(size)}")

    return counts


def build_dashboard_copies(only: set[str] | None) -> dict:
    """Genera las versiones simplificadas que consume el navegador."""
    print("\nSimplificando para el dashboard…")
    stats = {}

    for name, tol in (("comunidades", TOLERANCE_CA), ("provincias", TOLERANCE_PROV)):
        src = GEO_DIR / f"{name}.geojson"
        if not src.exists():
            print(f"  {name}: falta el fichero full-res, se omite")
            continue
        fc = json.loads(src.read_text(encoding="utf-8"))
        before = sum(count_vertices(f.get("geometry")) for f in fc["features"])
        simp = simplify_feature_collection(fc, tol)
        after = sum(count_vertices(f.get("geometry")) for f in simp["features"])
        size = write_json(DASH_GEO_DIR / f"{name}.geojson", simp)
        src_size = src.stat().st_size
        print(f"  {name:12} {before:7,} → {after:6,} vértices  "
              f"({_fmt_size(src_size)} → {_fmt_size(size)})")
        stats[name] = {"vertices_before": before, "vertices_after": after,
                       "bytes_full": src_size, "bytes_simplified": size}

    src_dir = GEO_DIR / "municipios"
    dst_dir = DASH_GEO_DIR / "municipios"
    if not src_dir.exists():
        print("  municipios: falta el directorio full-res, se omite")
        return stats

    files = sorted(src_dir.glob("*.geojson"))
    if only:
        files = [f for f in files if f.stem in only]
    tot_before = tot_after = tot_full = tot_simp = 0
    for f in files:
        fc = json.loads(f.read_text(encoding="utf-8"))
        before = sum(count_vertices(x.get("geometry")) for x in fc["features"])
        simp = simplify_feature_collection(fc, TOLERANCE_MUN)
        after = sum(count_vertices(x.get("geometry")) for x in simp["features"])
        size = write_json(dst_dir / f.name, simp)
        tot_before += before
        tot_after += after
        tot_full += f.stat().st_size
        tot_simp += size
    print(f"  municipios   {tot_before:7,} → {tot_after:6,} vértices en {len(files)} ficheros "
          f"({_fmt_size(tot_full)} → {_fmt_size(tot_simp)})")
    stats["municipios"] = {"files": len(files), "vertices_before": tot_before,
                           "vertices_after": tot_after, "bytes_full": tot_full,
                           "bytes_simplified": tot_simp}
    return stats


def build_index(provincias: list[dict], mun_counts: dict, stats: dict) -> None:
    """Escribe un índice con la jerarquía CCAA → provincia y metadatos de la fuente."""
    ca_map: dict[str, dict] = {}
    for p in provincias:
        ca = ca_map.setdefault(
            p["acom_code"], {"code": p["acom_code"], "name": p["acom_name"], "provincias": []}
        )
        ca["provincias"].append({
            "code": p["prov_code"],
            "name": p["prov_name"],
            "municipios": mun_counts.get(p["prov_code"]),
        })
    for ca in ca_map.values():
        ca["provincias"].sort(key=lambda x: x["name"])

    index = {
        "source": SOURCE_ATTRIBUTION,
        "datasets": {"comunidades": DATASET_CA, "provincias": DATASET_PROV,
                      "municipios": DATASET_MUN},
        "crs": "EPSG:4326",
        "simplification_tolerance_deg": {
            "comunidades": TOLERANCE_CA,
            "provincias": TOLERANCE_PROV,
            "municipios": TOLERANCE_MUN,
        },
        "totals": {
            "comunidades": len(ca_map),
            "provincias": len(provincias),
            "municipios": sum(v for v in mun_counts.values() if v),
        },
        "simplification_stats": stats,
        "comunidades": sorted(ca_map.values(), key=lambda x: x["name"]),
    }
    write_json(GEO_DIR / "index.json", index, compact=False)

    # El navegador solo necesita la jerarquía, no los metadatos de simplificación.
    write_json(
        DASH_GEO_DIR / "index.json",
        {
            "source": SOURCE_ATTRIBUTION,
            "comunidades": index["comunidades"],
        },
        compact=False,
    )
    print(f"\nÍndice: {len(ca_map)} CCAA, {len(provincias)} provincias, "
          f"{index['totals']['municipios']:,} municipios")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true",
                    help="vuelve a descargar aunque el fichero ya exista")
    ap.add_argument("--only", default="",
                    help="lista de códigos de provincia separados por comas (ej. 09,15,28)")
    ap.add_argument("--simplify-only", action="store_true",
                    help="no descarga nada, solo regenera las copias del dashboard")
    args = ap.parse_args()

    only = {c.strip().zfill(2) for c in args.only.split(",") if c.strip()} or None

    if args.simplify_only:
        prov_path = GEO_DIR / "provincias.geojson"
        if not prov_path.exists():
            print("No hay datos descargados todavía. Ejecuta sin --simplify-only.",
                  file=sys.stderr)
            return 1
        provincias = [f["properties"] for f in
                      json.loads(prov_path.read_text(encoding="utf-8"))["features"]]
        provincias.sort(key=lambda p: p["prov_code"])
        mun_counts = {}
        for f in sorted((GEO_DIR / "municipios").glob("*.geojson")):
            mun_counts[f.stem] = len(json.loads(f.read_text(encoding="utf-8"))["features"])
        stats = build_dashboard_copies(only)
        build_index(provincias, mun_counts, stats)
        return 0

    GEO_DIR.mkdir(parents=True, exist_ok=True)

    fetch_comunidades(args.force)
    prov_fc = fetch_provincias(args.force)

    provincias = list_provincias()
    if not provincias:
        # Plan B: sacar la lista del propio GeoJSON de provincias.
        provincias = [f["properties"] for f in prov_fc["features"]]
        provincias.sort(key=lambda p: p["prov_code"])

    mun_counts = fetch_municipios(provincias, args.force, only)
    stats = build_dashboard_copies(only)
    build_index(provincias, mun_counts, stats)

    print("\nListo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
