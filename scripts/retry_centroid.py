#!/usr/bin/env python3 -u
"""Reintenta geocodificación fina para entradas que quedaron con centroide."""

import json
import sys
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geocode_puntos import (
    extract_location_hint, build_geocode_queries, nominatim_geocode,
    haversine_km, load_municipality_centroids, load_province_names,
    FINE_GEOCODE_CACHE, MAX_DISTANCE_KM
)
from geopy.geocoders import Nominatim

# Cargar datos
with open(FINE_GEOCODE_CACHE) as f:
    cache = json.load(f)

centroids = load_municipality_centroids()
province_names = load_province_names()
geolocator = Nominatim(user_agent="calidad-agua-geocode-puntos/1.1")

# Cargar nombres de municipios
csvdir = Path(__file__).resolve().parent.parent / "data" / "raw" / "csvs"
muni_names = {}
for f in sorted(csvdir.glob("*.csv")):
    df = pd.read_csv(f, dtype=str, usecols=["provincia_code", "municipio_code", "municipio"])
    for _, row in df.drop_duplicates(["provincia_code", "municipio_code"]).iterrows():
        muni_names[(row["provincia_code"], row["municipio_code"])] = row["municipio"]

# Encontrar entradas centroide que ahora tienen hint con la nueva regex
to_retry = []
for key, val in cache.items():
    if val["source"] != "centroide":
        continue
    parts = key.split("_", 2)
    if len(parts) < 3:
        continue
    prov, muni_code, punto = parts[0], parts[1], parts[2]

    centroid = centroids.get((prov, muni_code))
    if centroid is None:
        continue

    muni = muni_names.get((prov, muni_code), "")
    hint = extract_location_hint(punto, muni)
    if hint is not None:
        to_retry.append({
            "key": key,
            "prov": prov,
            "muni_code": muni_code,
            "punto": punto,
            "municipio": muni,
            "hint": hint,
            "centroid": centroid,
        })

print(f"Entradas centroide en cache con hint útil (nueva regex): {len(to_retry)}")
print(f"Reintentando primeros 100...\n")

resolved = []
failed = []

for i, c in enumerate(to_retry[:100]):
    prov_name = province_names.get(c["prov"], "")
    queries = build_geocode_queries(c["hint"], c["municipio"], prov_name)

    found = False
    for query in queries:
        coords = nominatim_geocode(geolocator, query)
        if coords:
            dist = haversine_km(c["centroid"][0], c["centroid"][1], coords[0], coords[1])
            if dist <= MAX_DISTANCE_KM:
                resolved.append({
                    "punto": c["punto"],
                    "municipio": c["municipio"],
                    "provincia": prov_name,
                    "hint": c["hint"],
                    "dist_km": round(dist, 1),
                })
                cache[c["key"]] = {
                    "lat": round(coords[0], 7),
                    "lon": round(coords[1], 7),
                    "source": "geocodificado",
                }
                found = True
                break

    if not found:
        failed.append({
            "punto": c["punto"],
            "municipio": c["municipio"],
            "provincia": prov_name,
            "hint": c["hint"],
        })

    if (i + 1) % 25 == 0:
        print(f"  Progreso: {i+1}/100 (resueltos: {len(resolved)}, fallos: {len(failed)})")

# Guardar cache actualizado
with open(FINE_GEOCODE_CACHE, "w") as f:
    json.dump(cache, f, ensure_ascii=False, indent=2)

print(f"\n=== RESULTADOS ===")
print(f"Resueltos: {len(resolved)}/100")
print(f"Fallidos:  {len(failed)}/100")

if resolved:
    print(f"\n--- RESUELTOS (primeros 15) ---")
    for r in resolved[:15]:
        h = r["hint"][:40]
        print(f"  {r['municipio']:20s} | hint=\"{h}\"  dist={r['dist_km']}km")

print(f"\n--- FALLIDOS ({len(failed)}) ---")
print(f"{'MUNICIPIO':20s} | {'PROVINCIA':15s} | {'PUNTO_MUESTREO':55s} | HINT ENVIADO")
print("-" * 135)
for f_ in failed:
    print(f"{f_['municipio'][:20]:20s} | {f_['provincia'][:15]:15s} | {f_['punto'][:55]:55s} | {f_['hint'][:40]}")
