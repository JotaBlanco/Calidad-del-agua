#!/usr/bin/env python3
"""Rescata puntos que quedaron en el centroide, emparejándolos con el nomenclátor.

Muchos nombres de punto contienen el nombre de la aldea/parroquia/pedanía
("PM-RED-AHEDO-FUENTE C/SAN ESTEBAN" -> AHEDO), que los geocodificadores online
no resolvieron pero que SÍ está en el nomenclátor de GeoNames. Este script
empareja esos tokens contra el nomenclátor, restringido a la misma provincia y
prefiriendo el mismo municipio, y valida el resultado con la regla geométrica
de scripts/geo_validate.py.

Reutiliza la limpieza existente de scripts/geocode_puntos.py
(extract_location_hint, _clean_light, GENERIC_HINTS, _strip_accents) — no la
reimplementa.

Es OFFLINE: no hace ninguna llamada a API.

Uso:
    python scripts/resolve_centroid_puntos.py                 # simulación (dry-run)
    python scripts/resolve_centroid_puntos.py --apply         # escribe el cache
    python scripts/resolve_centroid_puntos.py --sample 30     # muestra para revisar a mano
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geocode_puntos import (  # noqa: E402
    GENERIC_HINTS, _clean_light, _strip_accents, extract_location_hint,
)
from geo_validate import (  # noqa: E402
    MunicipalTerms, haversine_km, is_infra_point, validate_candidate,
)

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "processed" / "geocoded_puntos_cache.json"
MUNI_CSV = ROOT / "data" / "processed" / "geocoded_municipalities.csv"
GAZ = ROOT / "data" / "geo" / "gazetteer" / "gazetteer_places.csv"

MIN_TOKEN_LEN = 4      # longitud mínima de un token de UNA palabra
MAX_NGRAM = 4          # n-gramas de hasta 4 palabras

# Palabras que nunca deben emparejar por sí solas: tipos de vía, elementos de
# red, y ruido administrativo. Complementa GENERIC_HINTS (que sólo compara la
# cadena COMPLETA, no token a token).
TOKEN_STOPWORDS = {
    # tipos de vía
    "CALLE", "CALLEJA", "CARRER", "RUA", "PLAZA", "PLAÇA", "PLACA", "PRAZA",
    "AVENIDA", "AVDA", "AVGDA", "CARRETERA", "CTRA", "CRTA", "CAMINO", "CAMI",
    "PASEO", "TRAVESIA", "TRAVESSERA", "RONDA", "GLORIETA", "PASAJE",
    "URBANIZACION", "POLIGONO", "PARCELA", "BARRIO", "BARRIADA", "NUCLEO",
    "CASERIO", "ALDEA", "LUGAR", "PARROQUIA", "PEDANIA", "DISEMINADO",
    # red / muestreo
    "RED", "REDE", "XARXA", "SARE", "DISTRIBUCION", "DISTRIBUCIO",
    "ABASTECIMIENTO", "MUESTREO", "MUESTRA", "MUESTRAS", "PUNTO", "PUNT",
    "GRIFO", "TORNEIRA", "AIXETA", "BOCA", "RIEGO", "SALIDA", "ENTRADA",
    "ARQUETA", "TORRETA", "HIDRANTE", "CONTADOR", "ACOMETIDA", "LLAVE",
    "VIGILANCIA", "SANITARIA", "CONTROL", "ANALISIS", "TOMA",
    # edificios / genéricos
    "FUENTE", "FUENTES", "FONT", "FONTE", "AYUNTAMIENTO", "AYTO",
    "AJUNTAMENT", "CONCELLO", "IGLESIA", "ESGLESIA", "ERMITA", "COLEGIO",
    "ESCUELA", "ESCUELAS", "CEIP", "CONSULTORIO", "MEDICO", "SALUD",
    "CENTRO", "CEMENTERIO", "POLIDEPORTIVO", "PISCINA", "PISCINAS", "PARQUE",
    "JARDIN", "FRONTON", "ASEO", "ASEOS", "VESTUARIOS", "COCINA", "BAR",
    "RESTAURANTE", "HOTEL", "CAMPING", "GASOLINERA", "FARMACIA", "CLUB",
    "RESIDENCIA", "VIVIENDA", "EDIFICIO", "NAVE", "CASETA", "CASA", "CASAS",
    "OFICINA", "OFICINAS", "LABORATORIO", "SERVICIOS", "MATADERO", "LAVADERO",
    "DEPOSITO", "PLANTA", "ESTACION", "SONDEO", "POZO", "POZOS",
    # posición / adjetivos
    "MUNICIPAL", "PUBLICA", "PUBLICO", "PARTICULAR", "PRINCIPAL", "GENERAL",
    "EXTERIOR", "INTERIOR", "ALTA", "ALTO", "BAJA", "BAJO", "ARRIBA", "ABAJO",
    "NUEVA", "NUEVO", "VIEJO", "VIEJA", "MAYOR", "MENOR", "URBANO", "CASCO",
    "PUEBLO", "VILLA", "ZONA", "PARTE", "JUNTO", "FRENTE", "OTRAS", "SOCIAL",
    "INFANTIL", "INDUSTRIAL", "DEPORTIVA", "AGUA", "AGUAS", "AIGUA",
    "ESPANA", "ESPAÑA", "NUMERO", "ESQUINA", "CONSUMIDOR",
    # preposiciones / artículos
    "DE", "DEL", "LA", "EL", "LOS", "LAS", "DO", "DA", "DOS", "DAS", "EN",
    "Y", "E", "O", "A", "AL", "SN", "CON", "SOBRE", "SANT", "SANTA", "SAN",
}

SEP_RE = re.compile(r"[^\wÁÉÍÓÚÜÑÀÈÌÒÙÇáéíóúüñàèìòùç]+", re.UNICODE)

# Indicadores de que lo que sigue es un ODÓNIMO (nombre de vía) o el nombre de
# una ORGANIZACIÓN, no la localidad donde está el punto. Medido sobre los
# matches cruzados: "BOCA DE RIEGO CTRA. BUEZO" (Buezo es otro municipio),
# "C/SILLA" en Picassent, "CAMINO RODA DE ERESMA" en Encinillas,
# "MCM CUARTE CADRETE MARIA" (mancomunidad) -> el punto NO está allí.
# Si uno de estos aparece antes en el mismo segmento, los tokens siguientes
# sólo pueden emparejar DENTRO del mismo municipio.
VIA_OR_ORG_INDICATORS = {
    "CTRA", "CRTA", "CARRETERA", "CAMINO", "CAMI", "CAMÍ", "CALLE", "C",
    "RUA", "CARRER", "AVDA", "AVENIDA", "AVGDA", "PLAZA", "PLACA", "PRAZA",
    "PZA", "PZ", "PL", "PGE", "PASEO", "TRAVESIA", "TRAVESSERA", "RONDA",
    "GLORIETA", "POLIGONO", "POL", "URBANIZACION", "URB",
    "CONEXION", "CONEXIO", "CRUCE", "ESQUINA",
    "MCM", "MANCOMUNIDAD", "MANCOMUNITAT", "MANCOMUNIDADE", "CONSORCIO",
    "COMUNIDAD", "COMARCA",
}


def norm(s: str) -> str:
    return _strip_accents(str(s)).upper().strip()


# ---------------------------------------------------------------------------
# Índice del nomenclátor
# ---------------------------------------------------------------------------

def build_gazetteer_index() -> tuple[dict, dict]:
    """Devuelve (por_provincia, por_municipio).

    por_provincia: {prov2: {nombre_norm: [entidad, ...]}}
    por_municipio: {mun5: {nombre_norm: [entidad, ...]}}

    Se EXCLUYEN las entidades de clase A (ADM3/ADM4): son el municipio en sí,
    así que emparejarlas devolvería otra vez un punto tipo centroide.
    """
    by_prov: dict[str, dict[str, list]] = collections.defaultdict(
        lambda: collections.defaultdict(list))
    by_mun: dict[str, dict[str, list]] = collections.defaultdict(
        lambda: collections.defaultdict(list))

    n_alt = 0
    with GAZ.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["feature_class"] != "P":
                continue
            prov = r["provincia_ine"]
            if not prov:
                continue
            mun = r["admin3"] if re.fullmatch(r"\d{5}", r["admin3"] or "") else None
            ent = {
                "name": r["name"],
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "fcode": r["feature_code"],
                "prov": prov,
                "mun": mun,
                "pop": int(r["population"] or 0),
            }
            names = {norm(r["name"]), norm(r["asciiname"])}
            # nombres alternativos: variantes gallega/catalana/euskera
            for alt in (r["alternatenames"] or "").split(","):
                alt = alt.strip()
                if len(alt) >= MIN_TOKEN_LEN and not alt.startswith("http"):
                    names.add(norm(alt))
                    n_alt += 1
            for nm in names:
                if not nm or len(nm) < MIN_TOKEN_LEN:
                    continue
                by_prov[prov][nm].append(ent)
                if mun:
                    by_mun[mun][nm].append(ent)
    print(f"  índice del nomenclátor: {sum(len(v) for v in by_prov.values()):,} "
          f"nombres por provincia ({n_alt:,} alternativos incluidos)")
    return by_prov, by_mun


# ---------------------------------------------------------------------------
# Generación de tokens candidatos
# ---------------------------------------------------------------------------

def candidate_tokens(punto: str, municipio: str) -> list[tuple[str, bool]]:
    """N-gramas candidatos, de más largo a más corto (los largos son más fiables).

    Devuelve [(gram, es_odonimo), ...]. es_odonimo=True cuando el token aparece
    tras un indicador de vía u organización (VIA_OR_ORG_INDICATORS) en el mismo
    segmento: esos tokens sólo pueden emparejar dentro del mismo municipio.
    """
    hint = extract_location_hint(punto, municipio, remove_municipio=False)
    light = _clean_light(punto)
    mun_n = norm(municipio)
    generic = {norm(x) for x in GENERIC_HINTS}

    grams: list[tuple[str, bool]] = []
    seen: dict[str, bool] = {}
    for text in (hint, light, punto):
        if not text:
            continue
        if norm(text) in generic:
            continue
        # "C/SILLA" -> "CALLE SILLA": si no, la barra separa el indicador de
        # vía de su nombre y el token se toma por una localidad
        # (medido: 'C/SILLA' en Picassent, 'C/ RODA DE ERESMA' en Los Huertos).
        text = re.sub(r"\b(C|AVDA?|AVGDA|CTRA|CRTA|PZA?|PL)\s*/\s*", r"\1 ",
                      text, flags=re.IGNORECASE)
        # trocear por separadores fuertes para no cruzar campos distintos
        for seg in re.split(r"[-–/,;()\|]+", text):
            words = [w for w in SEP_RE.split(seg) if w]
            words = [norm(w) for w in words]
            words = [w for w in words if w and not w.isdigit()]
            n = len(words)
            # primer índice a partir del cual todo es odónimo/organización
            odo_from = n
            for idx, w in enumerate(words):
                if w in VIA_OR_ORG_INDICATORS:
                    odo_from = min(odo_from, idx + 1)
            for size in range(min(MAX_NGRAM, n), 0, -1):
                for i in range(n - size + 1):
                    gram = " ".join(words[i:i + size])
                    if size == 1:
                        w = words[i]
                        if len(w) < MIN_TOKEN_LEN or w in TOKEN_STOPWORDS:
                            continue
                    else:
                        # descarta n-gramas que son todo stopwords
                        if all(w in TOKEN_STOPWORDS for w in words[i:i + size]):
                            continue
                    # nunca emparejar el propio municipio: devolvería el centroide
                    if gram == mun_n:
                        continue
                    is_odo = i >= odo_from
                    if gram in seen:
                        # si en algún sitio aparece sin indicador de vía, vale
                        seen[gram] = seen[gram] and is_odo
                        continue
                    seen[gram] = is_odo
                    grams.append((gram, is_odo))
    # más palabras primero, y a igual número de palabras, más largo primero
    grams.sort(key=lambda g: (g[0].count(" "), len(g[0])), reverse=True)
    return [(g, seen[g]) for g, _ in grams]


# ---------------------------------------------------------------------------
# Emparejamiento
# ---------------------------------------------------------------------------

def match_point(punto, municipio, mun_code, prov_code, centroid,
                by_prov, by_mun, terms):
    """Devuelve dict con el match aceptado, o None."""
    mun_n = norm(municipio)
    for gram, is_odonym in candidate_tokens(punto, municipio):
        # 1) mismo municipio (preferente)  2) misma provincia
        for scope, index in (("municipio", by_mun.get(mun_code, {})),
                             ("provincia", by_prov.get(prov_code, {}))):
            # un token que es nombre de vía u organización sólo vale dentro
            # del propio municipio (ver VIA_OR_ORG_INDICATORS)
            if is_odonym and scope != "municipio":
                continue
            ents = index.get(gram)
            if not ents:
                continue
            # el más poblado primero: desempate estable y razonable
            for ent in sorted(ents, key=lambda e: -e["pop"]):
                if norm(ent["name"]) == mun_n:
                    continue  # la entidad es el propio municipio
                ok, verdict, dist = validate_candidate(
                    ent["lat"], ent["lon"], mun_code, centroid, punto, terms)
                if not ok:
                    continue
                # Endurecimiento propio del emparejamiento por nombre: un match
                # aceptado sólo por la exención de infraestructura (fuera del
                # término y de la banda de gracia) debe además estar en el
                # mismo municipio, o el riesgo de falso positivo es alto.
                if verdict in ("infra_exempt", "no_polygon") and scope != "municipio":
                    continue
                return {
                    "lat": ent["lat"], "lon": ent["lon"],
                    "entity": ent["name"], "fcode": ent["fcode"],
                    "gram": gram, "scope": scope, "verdict": verdict,
                    "dist_km": dist,
                    "ent_mun": ent["mun"], "pop": ent["pop"],
                }
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="escribe el cache")
    ap.add_argument("--sample", type=int, default=0,
                    help="imprime N matches aleatorios para revisión manual")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--muni-dir", default=None)
    ap.add_argument("--out-report", default=None,
                    help="CSV con todos los matches, para auditoría")
    args = ap.parse_args()

    terms = MunicipalTerms(Path(args.muni_dir) if args.muni_dir else None)
    cache = json.loads(CACHE.read_text(encoding="utf-8"))

    mdf = pd.read_csv(MUNI_CSV, dtype={"provincia_code": str, "municipio_code": str})
    centroids, names = {}, {}
    for r in mdf.itertuples(index=False):
        code = str(r.municipio_code).zfill(5)
        centroids[code] = (float(r.lat_municipio), float(r.lon_municipio))
        names[code] = r.municipio

    print("construyendo índice del nomenclátor...")
    by_prov, by_mun = build_gazetteer_index()

    targets = [(k, v) for k, v in cache.items() if v.get("source") == "centroide"]
    print(f"\npuntos en el centroide a rescatar: {len(targets):,}")

    stats = collections.Counter()
    matches = []
    for key, val in targets:
        parts = key.split("_", 2)
        if len(parts) != 3:
            stats["clave malformada"] += 1
            continue
        prov_code = parts[0].zfill(2)
        mun_code = parts[1].zfill(5)
        punto = parts[2]
        centroid = centroids.get(mun_code)
        if centroid is None:
            stats["sin centroide municipal"] += 1
            continue
        municipio = names.get(mun_code, "")
        m = match_point(punto, municipio, mun_code, prov_code, centroid,
                        by_prov, by_mun, terms)
        if m is None:
            stats["sin match"] += 1
            continue
        stats["RESCATADO"] += 1
        stats[f"  scope={m['scope']}"] += 1
        stats[f"  verdict={m['verdict']}"] += 1
        m.update(key=key, punto=punto, municipio=municipio,
                 mun_code=mun_code, prov_code=prov_code,
                 centroid_dist_km=haversine_km(centroid[0], centroid[1],
                                               m["lat"], m["lon"]))
        matches.append(m)

    print()
    total = len(targets)
    for k in sorted(stats, key=lambda x: (not x.startswith("RESC"), x)):
        print(f"  {k:28} {stats[k]:6,}"
              + (f"  {100*stats[k]/total:5.1f}%" if not k.startswith("  ") else ""))

    if terms.missing_provinces:
        print(f"\n  !! provincias sin polígono: {sorted(terms.missing_provinces)}")

    # --- muestra para revisión manual -------------------------------------
    if args.sample and matches:
        rnd = random.Random(args.seed)
        sel = rnd.sample(matches, min(args.sample, len(matches)))
        print("\n" + "=" * 100)
        print(f"MUESTRA ALEATORIA DE {len(sel)} MATCHES (seed={args.seed}) — revisar a mano")
        print("=" * 100)
        for i, m in enumerate(sel, 1):
            same = "MISMO MUN" if m["ent_mun"] == m["mun_code"] else f"mun={m['ent_mun']}"
            print(f"\n{i:2}. punto     : {m['punto']}")
            print(f"    municipio : {m['municipio']} ({m['mun_code']})")
            print(f"    token     : '{m['gram']}'  ->  entidad '{m['entity']}'"
                  f" [{m['fcode']}, {same}, pop={m['pop']}]")
            print(f"    veredicto : {m['verdict']} ({m['dist_km']:.2f} km) | "
                  f"a {m['centroid_dist_km']:.2f} km del centroide | "
                  f"{m['lat']:.5f},{m['lon']:.5f}")

    # --- CSV de auditoría --------------------------------------------------
    if args.out_report and matches:
        cols = ["key", "prov_code", "mun_code", "municipio", "punto", "gram",
                "entity", "fcode", "scope", "verdict", "dist_km",
                "centroid_dist_km", "ent_mun", "pop", "lat", "lon"]
        with open(args.out_report, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(matches)
        print(f"\ninforme -> {args.out_report}")

    # --- aplicar -----------------------------------------------------------
    if args.apply and matches:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = CACHE.with_suffix(f".json.bak_{stamp}")
        shutil.copy2(CACHE, backup)
        print(f"\nbackup -> {backup.name}")
        for m in matches:
            cache[m["key"]] = {
                "lat": m["lat"], "lon": m["lon"],
                "source": f"nomenclator (geonames/{m['scope']})",
            }
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        print(f"cache actualizado: {len(matches):,} puntos pasan de "
              f"'centroide' a 'nomenclator'")
    elif not args.apply:
        print("\n(dry-run — usa --apply para escribir el cache)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
