#!/usr/bin/env python3
"""Verifica geométricamente el join gazetteer(GeoNames) -> municipio INE.

Hipótesis a validar: el campo admin3 del dump de GeoNames para España ES el
código INE de municipio de 5 dígitos.

Prueba independiente: para cada entidad del gazetteer, comprobar si su
coordenada cae DENTRO del polígono del término municipal admin3. Si el join
fuese falso, la tasa de acierto sería próxima al azar (~1/8131).

Uso:
    python scripts/verify_gazetteer_join.py [--sample N] [--muni-dir DIR]
"""
from __future__ import annotations

import argparse
import collections
import random
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo_validate import MunicipalTerms, dist_to_boundary_km  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GAZ = ROOT / "data" / "geo" / "gazetteer" / "gazetteer_places.csv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="0 = todas las entidades")
    ap.add_argument("--muni-dir", default=None)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    terms = MunicipalTerms(Path(args.muni_dir) if args.muni_dir else None)
    g = pd.read_csv(GAZ, dtype=str)
    g = g[g.admin3.notna() & g.admin3.str.fullmatch(r"\d{5}")].copy()
    g["lat"] = g.lat.astype(float)
    g["lon"] = g.lon.astype(float)

    recs = g.to_dict("records")
    if args.sample and args.sample < len(recs):
        random.Random(args.seed).shuffle(recs)
        recs = recs[: args.sample]

    res = collections.Counter()
    far = []
    for r in recs:
        code = r["admin3"]
        t = terms.get(code)
        if not t:
            res["sin poligono"] += 1
            continue
        inside = terms.contains(code, r["lon"], r["lat"])
        if inside:
            res["DENTRO del municipio admin3"] += 1
            continue
        d = dist_to_boundary_km(r["lon"], r["lat"], t["polys"])
        if d <= 1:
            res["fuera <=1 km (borde)"] += 1
        elif d <= 5:
            res["fuera 1-5 km"] += 1
        else:
            res["fuera >5 km"] += 1
            far.append((d, r["name"], code, t["name"]))

    evaluated = sum(v for k, v in res.items() if k != "sin poligono")
    print(f"entidades del gazetteer evaluadas: {evaluated:,}")
    print(f"  (sin polígono municipal disponible: {res['sin poligono']:,})")
    if terms.missing_provinces:
        print(f"  provincias sin fichero: {sorted(terms.missing_provinces)}")
    print()
    for k in ["DENTRO del municipio admin3", "fuera <=1 km (borde)",
              "fuera 1-5 km", "fuera >5 km"]:
        if res[k]:
            print(f"  {k:30} {res[k]:7,}  {100*res[k]/evaluated:5.2f}%")

    good = res["DENTRO del municipio admin3"] + res["fuera <=1 km (borde)"]
    print(f"\n  join correcto (dentro o a <=1 km del borde): "
          f"{good:,}/{evaluated:,} = {100*good/evaluated:.2f}%")
    print(f"  azar esperado si el join fuese falso: ~{100/8131:.3f}%")

    if far:
        print("\npeores discrepancias (>5 km fuera):")
        for d, name, code, mname in sorted(far, reverse=True)[:12]:
            print(f"  {d:8.1f} km  {name[:30]:30} admin3={code} ({mname})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
