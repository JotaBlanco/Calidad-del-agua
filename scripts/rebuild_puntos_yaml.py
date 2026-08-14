#!/usr/bin/env python3
"""Regenera data/processed/puntos_coordenadas.yaml desde los caches, sin API.

puntos_coordenadas.yaml es la salida derivada que consume el dashboard
(scripts/build_dashboard_data.py). Normalmente lo escribe la fase [4/4] de
scripts/geocode_puntos.py, pero eso exige recorrer todo el dataset y puede
lanzar llamadas a las APIs de geocodificación.

Este script sólo re-proyecta el YAML existente sobre los caches ya
actualizados, así que es totalmente offline:
  - coordenada y source de cada punto  <- geocoded_puntos_cache.json
  - centroide de cada municipio        <- geocoded_municipalities.csv

Uso:
    python scripts/rebuild_puntos_yaml.py            # simulación
    python scripts/rebuild_puntos_yaml.py --apply    # escribe (con backup)
"""
from __future__ import annotations

import argparse
import collections
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

try:  # el loader/dumper en C es mucho más rápido
    from yaml import CSafeDumper as Dumper, CSafeLoader as Loader
except ImportError:  # pragma: no cover
    from yaml import SafeDumper as Dumper, SafeLoader as Loader

ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = ROOT / "data" / "processed" / "puntos_coordenadas.yaml"
CACHE = ROOT / "data" / "processed" / "geocoded_puntos_cache.json"
MUNI_CSV = ROOT / "data" / "processed" / "geocoded_municipalities.csv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    import json
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    mdf = pd.read_csv(MUNI_CSV, dtype={"provincia_code": str, "municipio_code": str})
    centroids = {str(r.municipio_code).zfill(5):
                 (float(r.lat_municipio), float(r.lon_municipio))
                 for r in mdf.itertuples(index=False)}

    print(f"cargando {YAML_PATH.name} ...", flush=True)
    doc = yaml.load(YAML_PATH.read_text(encoding="utf-8"), Loader=Loader)

    stats = collections.Counter()
    for muni in doc["municipios"]:
        prov = str(muni["provincia_code"])
        mun5 = str(muni["municipio_code"]).zfill(5)
        c = centroids.get(mun5)
        if c:
            muni["centroide"] = {"lat": round(c[0], 7), "lon": round(c[1], 7)}
            stats["centroide actualizado"] += 1
        else:
            stats["municipio sin centroide"] += 1
        # la clave del cache usa códigos SIN ceros a la izquierda
        kp, km = str(int(prov)), str(int(mun5))
        for p in muni["puntos_muestreo"]:
            key = f"{kp}_{km}_{p['nombre']}"
            hit = cache.get(key)
            if hit is None:
                stats["punto no está en el cache"] += 1
                continue
            p["lat"], p["lon"], p["source"] = hit["lat"], hit["lon"], hit["source"]
            src = hit["source"]
            stats["  " + ("centroide" if src == "centroide"
                          else "nomenclator" if src.startswith("nomenclator")
                          else "geocodificado")] += 1
            stats["punto actualizado"] += 1

    print()
    for k in sorted(stats):
        print(f"  {k:28} {stats[k]:7,}")

    if args.apply:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = YAML_PATH.with_suffix(f".yaml.bak_{stamp}")
        shutil.copy2(YAML_PATH, backup)
        print(f"\nbackup -> {backup.name}")
        with YAML_PATH.open("w", encoding="utf-8") as fh:
            yaml.dump(doc, fh, Dumper=Dumper, default_flow_style=False,
                      allow_unicode=True, sort_keys=False, width=120)
        print(f"escrito {YAML_PATH.name}")
    else:
        print("\n(simulación — usa --apply para escribir)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
