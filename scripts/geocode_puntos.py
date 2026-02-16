#!/usr/bin/env python3 -u
"""
Genera un YAML con las coordenadas de cada punto de medición.

Estrategia en dos pasadas:
  1. Geocodifica los centroides de municipios que falten en el cache.
     Asigna el centroide del municipio a todos los puntos.
  2. Intenta geocodificar cada punto usando su nombre/dirección con Nominatim.
     Si el resultado queda a una distancia razonable del centroide (< MAX_DISTANCE_KM),
     se usa la coordenada fina; si no, se mantiene el centroide.

Los resultados de geocodificación se cachean para poder reanudar sin repetir
consultas a la API.

Uso:
    python scripts/geocode_puntos.py                  # ejecutar todo
    python scripts/geocode_puntos.py --max-points 50  # limitar puntos fine-geocode (prueba)
    python scripts/geocode_puntos.py --skip-fine       # solo centroides, sin 2ª pasada
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import unicodedata
from pathlib import Path

import urllib.parse
import urllib.request

import pandas as pd
import yaml
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CSV_DIR = DATA_DIR / "raw" / "csvs"
CACHE_DIR = DATA_DIR / "processed"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MUNI_GEOCODE_CACHE = CACHE_DIR / "geocoded_municipalities.csv"
FINE_GEOCODE_CACHE = CACHE_DIR / "geocoded_puntos_cache.json"
OUTPUT_YAML = DATA_DIR / "processed" / "puntos_coordenadas.yaml"

CATALOG_PATH = DATA_DIR / "locations_catalog.csv"

MAX_DISTANCE_KM = 50  # distancia máxima aceptable del centroide
NOMINATIM_DELAY = 1.1  # segundos entre peticiones (política de Nominatim)
MAX_RETRIES = 3

# Claves API opcionales (proveedores gratuitos con registro)
LOCATIONIQ_KEY = ""  # https://locationiq.com (5000 req/día gratis)
GEOCODE_MAPS_KEY = ""  # https://geocode.maps.co (25000/mes gratis)
OPENCAGE_KEY = ""  # https://opencagedata.com (2500/día trial)

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en km entre dos puntos (fórmula de Haversine)."""
    R = 6371.0
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _http_get_json(url: str, timeout: int = 10) -> dict | list | None:
    """Fetch JSON from URL, returning None on any error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "calidad-agua-geocode/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Proveedores de geocodificación
# ---------------------------------------------------------------------------

def geocode_nominatim(geolocator: Nominatim, query: str) -> tuple[float, float] | None:
    """Geocodifica con Nominatim (OSM). 1 req/s rate limit."""
    for attempt in range(MAX_RETRIES):
        try:
            time.sleep(NOMINATIM_DELAY)
            loc = geolocator.geocode(query, timeout=10)
            if loc:
                return (loc.latitude, loc.longitude)
            return None
        except (GeocoderTimedOut, GeocoderServiceError):
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            continue
    return None


def geocode_cartociudad(query: str) -> tuple[float, float] | None:
    """Geocodifica con CartoCiudad (IGN España). Sin API key, sin límite."""
    url = (
        "https://www.cartociudad.es/geocoder/api/geocoder/find?"
        + urllib.parse.urlencode({"q": query})
    )
    time.sleep(0.3)  # cortesía
    data = _http_get_json(url)
    if not data or not isinstance(data, dict):
        return None
    lat = data.get("lat")
    lng = data.get("lng")
    if lat and lng and float(lat) != 0.0:
        return (float(lat), float(lng))
    return None


def geocode_cartociudad_candidates(query: str) -> tuple[float, float] | None:
    """Geocodifica con CartoCiudad candidates endpoint.

    NOTE: This endpoint often returns lat=0.0, lng=0.0 (no real coordinates).
    We filter those out. The `find` endpoint is generally more reliable.
    """
    url = (
        "https://www.cartociudad.es/geocoder/api/geocoder/candidates?"
        + urllib.parse.urlencode({"q": query, "limit": "5"})
    )
    time.sleep(0.3)
    data = _http_get_json(url)
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    # candidates often returns lat=0.0 — find first item with real coords
    for item in data:
        lat = item.get("lat")
        lng = item.get("lng")
        if lat is not None and lng is not None:
            flat, flng = float(lat), float(lng)
            if flat != 0.0 and flng != 0.0:
                return (flat, flng)
    return None


def geocode_photon(query: str) -> tuple[float, float] | None:
    """Geocodifica con Photon (Komoot). Sin API key."""
    url = (
        "https://photon.komoot.io/api?"
        + urllib.parse.urlencode({"q": query, "limit": "1"})
    )
    time.sleep(1.1)  # rate limit cortesía
    data = _http_get_json(url)
    if not data or "features" not in data:
        return None
    features = data["features"]
    if features:
        coords = features[0].get("geometry", {}).get("coordinates")
        if coords and len(coords) >= 2:
            return (float(coords[1]), float(coords[0]))  # [lon, lat] → (lat, lon)
    return None


def geocode_locationiq(query: str) -> tuple[float, float] | None:
    """Geocodifica con LocationIQ. Requiere API key gratuita."""
    if not LOCATIONIQ_KEY:
        return None
    url = (
        "https://us1.locationiq.com/v1/search?"
        + urllib.parse.urlencode({
            "key": LOCATIONIQ_KEY,
            "q": query,
            "format": "json",
            "limit": "1",
        })
    )
    time.sleep(0.5)  # 2 req/s limit
    data = _http_get_json(url)
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    item = data[0]
    lat = item.get("lat")
    lon = item.get("lon")
    if lat is not None and lon is not None:
        return (float(lat), float(lon))
    return None


def geocode_maps_co(query: str) -> tuple[float, float] | None:
    """Geocodifica con geocode.maps.co. Requiere API key gratuita."""
    if not GEOCODE_MAPS_KEY:
        return None
    url = (
        "https://geocode.maps.co/search?"
        + urllib.parse.urlencode({
            "api_key": GEOCODE_MAPS_KEY,
            "q": query,
            "limit": "1",
        })
    )
    time.sleep(1.1)  # 1 req/s on free
    data = _http_get_json(url)
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    item = data[0]
    lat = item.get("lat")
    lon = item.get("lon")
    if lat is not None and lon is not None:
        return (float(lat), float(lon))
    return None


def geocode_opencage(query: str) -> tuple[float, float] | None:
    """Geocodifica con OpenCage. Requiere API key (trial gratuito)."""
    if not OPENCAGE_KEY:
        return None
    url = (
        "https://api.opencagedata.com/geocode/v1/json?"
        + urllib.parse.urlencode({
            "key": OPENCAGE_KEY,
            "q": query,
            "limit": "1",
            "language": "es",
            "countrycode": "es",
        })
    )
    time.sleep(1.1)  # 1 req/s trial limit
    data = _http_get_json(url)
    if not data or "results" not in data:
        return None
    results = data["results"]
    if results:
        geo = results[0].get("geometry", {})
        lat = geo.get("lat")
        lng = geo.get("lng")
        if lat is not None and lng is not None:
            return (float(lat), float(lng))
    return None


def _try_providers(
    geolocator: Nominatim,
    queries: list[str],
    centroid: tuple[float, float],
    carto_queries: list[str] | None = None,
) -> tuple[float, float, str] | None:
    """Try all providers with the given query list. Returns (lat, lon, provider) or None.

    carto_queries: separate queries for CartoCiudad (without "España",
    since CartoCiudad is Spain-only and chokes on it).
    """
    # 1. Photon (best performer: 64% at full clean)
    for query in queries:
        coords = geocode_photon(query)
        if coords:
            dist = haversine_km(centroid[0], centroid[1], coords[0], coords[1])
            if dist <= MAX_DISTANCE_KM:
                return (coords[0], coords[1], "photon")

    # 2. Nominatim (23% at full clean)
    for query in queries:
        coords = geocode_nominatim(geolocator, query)
        if coords:
            dist = haversine_km(centroid[0], centroid[1], coords[0], coords[1])
            if dist <= MAX_DISTANCE_KM:
                return (coords[0], coords[1], "nominatim")

    # 3. CartoCiudad (find only — candidates endpoint returns lat=0)
    #    Uses carto_queries (no "España") or falls back to standard queries
    for query in (carto_queries or queries):
        coords = geocode_cartociudad(query)
        if coords:
            dist = haversine_km(centroid[0], centroid[1], coords[0], coords[1])
            if dist <= MAX_DISTANCE_KM:
                return (coords[0], coords[1], "cartociudad")

    # 4-6. Optional API-key providers
    for query in queries:
        for prov_name, prov_fn in [
            ("locationiq", geocode_locationiq),
            ("geocode_maps_co", geocode_maps_co),
            ("opencage", geocode_opencage),
        ]:
            try:
                coords = prov_fn(query)
            except Exception:
                continue
            if coords:
                dist = haversine_km(centroid[0], centroid[1], coords[0], coords[1])
                if dist <= MAX_DISTANCE_KM:
                    return (coords[0], coords[1], prov_name)

    return None


def geocode_multi(
    geolocator: Nominatim,
    levels: list[tuple[str, list[str], list[str]]],
    centroid: tuple[float, float],
) -> tuple[float, float, str, str] | None:
    """
    Intenta geocodificar usando múltiples niveles de limpieza,
    cada uno probando todos los proveedores antes de pasar al siguiente nivel.

    levels: list of (level_name, queries, carto_queries) tuples.
    Devuelve (lat, lon, provider_name, level_name) o None si todos fallan.
    """
    for level_name, queries, carto_queries in levels:
        result = _try_providers(geolocator, queries, centroid, carto_queries)
        if result:
            return (result[0], result[1], result[2], level_name)

    return None


def _strip_accents(s: str) -> str:
    """Remove diacritics/accents from a string for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def extract_location_hint(
    punto: str, municipio: str, remove_municipio: bool = True
) -> str | None:
    """
    Extrae una pista de ubicación del nombre del punto de muestreo.
    Busca calles, plazas, barrios u otros topónimos útiles para geocodificar.
    Devuelve None si no se encuentra nada útil.

    remove_municipio: if False, keep the municipality name in the output
    (matrix tests showed removing it hurts all geocoding providers).
    """
    if not isinstance(punto, str) or not punto.strip():
        return None
    if punto.strip().lower() == "nan":
        return None

    # --- Normalizar separadores ---
    # Underscore -> espacio (PM_AYUNTAMIENTO, PM_XARXA, etc.)
    punto = punto.replace("_", " ")

    # --- Prefijos ---
    # PM, PM1, PM-2, E-PM-RD, M-PM-RD, PTMF, CP-RED, etc.
    punto = re.sub(
        r"^(E-|M-)?PM\w?\d*\s*[-–.]?\s*", "", punto, flags=re.IGNORECASE
    ).strip()
    punto = re.sub(
        r"^PTMF?\d*\s*[-–.]?\s*", "", punto, flags=re.IGNORECASE
    ).strip()
    punto = re.sub(
        r"^CP\s*[-–.]?\s*", "", punto, flags=re.IGNORECASE
    ).strip()
    # PTO. MUESTREO / PUNTO DE MUESTREO
    punto = re.sub(
        r"^(PTO\.?\s*MUESTREO|PUNTO\s+DE\s+MUESTREO)\s*[-–]?\s*",
        "",
        punto,
        flags=re.IGNORECASE,
    ).strip()
    # TORRE (TOMA) MUESTRA / ESTACIÓN TOMA DE MUESTRA
    punto = re.sub(
        r"^(TORRE\s+(TOMA\s+)?MUESTRA|ESTACI[OÓ]N\s+TOMA\s+DE\s+MUESTRA)\s*[-–.]?\s*",
        "",
        punto,
        flags=re.IGNORECASE,
    ).strip()
    # RED DE DISTRIBUCIÓN / RED DE ABASTECIMIENTO / RED / RD (al inicio)
    punto = re.sub(
        r"^(RED\s+(DE\s+)?(DISTRIBUCIÓN|DISTRIBUCION|ABASTECIMIENTO)|RED|RD)\s*[-–.,]?\s*",
        "",
        punto,
        flags=re.IGNORECASE,
    ).strip()
    # Códigos de empresa: AQS, AQC, AQN, CGC, EMACSA, AQUAGEST, GESTAGUA, APRESA, HIDROGESTIÓN, etc.
    punto = re.sub(
        r"^(AQS|AQC|AQN|CGC|EMACSA|AQUAGEST\s*\w*|GESTAGUA|APRESA|HIDROGESTI[OÓ]N\s*S\.?A\.?)\s*[-–,]?\s*",
        "",
        punto,
        flags=re.IGNORECASE,
    ).strip()
    # Prefijos tipo ECC-2, ECC-3
    punto = re.sub(
        r"^ECC-?\d+\s*[-–.]?\s*", "", punto, flags=re.IGNORECASE
    ).strip()

    # --- Prefijos internos de tipo de punto (before municipality removal) ---
    # AYTO, AYTO-, TUT, EST.
    punto = re.sub(
        r"^(AYTO\.?|TUT|EST\.?)\s*[-–]?\s*", "", punto, flags=re.IGNORECASE
    ).strip()

    # --- Sufijos ---
    # /ZA, -ZA, (ZA, ,ZA y todo lo que siga
    punto = re.sub(
        r"\s*[-–/(,\s]\s*(ZA|ZONA\s+ABAST)\b.*$", "", punto, flags=re.IGNORECASE
    ).strip()
    # Sufijo tipo "-CR", "-CU-", "-AB", "-GU" (códigos de provincia, may have trailing dash)
    punto = re.sub(r"\s*[-–,]\s*[A-Z]{1,2}\s*[-–]?\s*$", "", punto).strip()
    # Contenido entre paréntesis con "Red" o "Abastecimiento" dentro
    punto = re.sub(
        r"\(.*?(RED|ABASTECIMIENTO|DISTRIBUC).*?\)", "", punto, flags=re.IGNORECASE
    ).strip()

    # --- Municipio patterns (always needed for RED {municipio} removal) ---
    muni_norm = _strip_accents(municipio)
    muni_pat = re.escape(muni_norm)

    # Build alternative municipality patterns (article forms)
    # "ACEBRÓN (EL)" → also match "EL ACEBRÓN"
    muni_patterns = [muni_pat]
    art_m = re.match(r"^(.*?)\s*\((\w+)\)\s*$", muni_norm)
    if art_m:
        name, art = art_m.groups()
        muni_patterns.append(re.escape(f"{art} {name}"))
        # Also match just the name without article
        muni_patterns.append(re.escape(name))

    def _remove_muni(text: str) -> str:
        """Remove municipality name from text (accent-insensitive)."""
        for pat in muni_patterns:
            tn = _strip_accents(text)
            # Al inicio
            m = re.match(rf"^{pat}\s*[-–.,]?\s*", tn, flags=re.IGNORECASE)
            if m:
                text = text[m.end():]
                tn = _strip_accents(text)
            # Precedido por separador (dash, comma, space)
            m2 = re.search(rf"\s*[-–, ]\s*{pat}\b", tn, flags=re.IGNORECASE)
            if m2:
                text = text[:m2.start()] + text[m2.end():]
        return text.strip()

    # Only remove municipality name if requested (matrix tests showed it hurts)
    if remove_municipio:
        punto = _remove_muni(punto)

    # --- RED handling ---
    # Remove "RED {municipio}" pattern (common in AQN entries) — always do this
    punto_norm = _strip_accents(punto)
    for mp in muni_patterns:
        m_red_muni = re.search(
            rf"\bRED\s+{mp}\b", punto_norm, flags=re.IGNORECASE
        )
        if m_red_muni:
            punto = punto[:m_red_muni.start()] + punto[m_red_muni.end():]
            punto_norm = _strip_accents(punto)
            break
    # Remove "RED DE DISTRIBUCIÓN/ABASTECIMIENTO..." and everything after
    punto = re.sub(
        r"\bRED\s+(DE\s+)?(DISTRIBUC\w*|ABASTECIMIENTO)\b.*$",
        "", punto, flags=re.IGNORECASE
    ).strip()
    # Remove standalone RED at end or preceded by separator
    punto = re.sub(r"\s*[-–/(.,]\s*RED\s*$", "", punto, flags=re.IGNORECASE).strip()
    punto = re.sub(r"^RED\s*[-–.,]?\s*", "", punto, flags=re.IGNORECASE).strip()
    # Remove RED (and optional "DE" after) as isolated word
    punto = re.sub(r"\bRED\s+(DE\s+)?", "", punto, flags=re.IGNORECASE).strip()
    punto = re.sub(r"\bRED\b", "", punto, flags=re.IGNORECASE).strip()

    # Second pass municipality removal (after RED stripping may expose it)
    if remove_municipio:
        punto = _remove_muni(punto)

    # --- Noise prefixes (before real address) ---
    # "SALIDA (DE) (FUENTE)" prefix
    punto = re.sub(
        r"^SALIDA\s+(DE\s+)?(FUENTE\s+)?", "", punto, flags=re.IGNORECASE
    ).strip()
    # "EXTERIOR (DE) (VIVIENDA)" / "VIVIENDA PARTICULAR"
    punto = re.sub(
        r"^(EXTERIOR\s+(DE\s+)?(VIVIENDA\s+)?|VIVIENDA\s+PARTICULAR\s*)",
        "", punto, flags=re.IGNORECASE
    ).strip()
    # "BOCA RIEGO" prefix
    punto = re.sub(
        r"^BOCA\s+RIEGO\s*[-–]?\s*", "", punto, flags=re.IGNORECASE
    ).strip()
    # "CASA (JUNTA VECINAL|PARTICULAR|CULTURA)" prefix — keep locality after
    punto = re.sub(
        r"^CASA\s+(JUNTA\s+VECINAL|PARTICULAR|CULTURA)\s+",
        "", punto, flags=re.IGNORECASE
    ).strip()
    # "DISPOSITIVO DE EXTRACCION UBICADO EN" / "CONDUCCION" verbose prefix
    punto = re.sub(
        r"^DISPOSITIVO\s+DE\s+EXTRACCION\s+UBICADO\s+EN\s+",
        "", punto, flags=re.IGNORECASE
    ).strip()
    # Leading number/code prefixes: "02 ", "5629# ", "F/S 01/01/17 ZLM 1."
    punto = re.sub(r"^\d{1,5}#?\s+", "", punto).strip()
    punto = re.sub(r"^F/S\s+\d[\d/]+\s+\w{2,4}\s+\d+\.?\s*", "", punto, flags=re.IGNORECASE).strip()
    # "FUENTE (PÚBLICA)?" when followed by a street/address/landmark indicator
    punto = re.sub(
        r"^FUENTE\s+(P[UÚ]BLICA\s+)?(PIL[OÓ]N\s+)?"
        r"(?=(C/|CALLE|PLAZA|PZA?\.?|PASEO|AVDA|AVENIDA|CTRA|CARRETERA|TRAV|RÚA|Bº|BARRIO|PRAZA|PARQUE))",
        "", punto, flags=re.IGNORECASE
    ).strip()
    # "FUENTE PÚBLICA" prefix when followed by more content
    punto = re.sub(
        r"^FUENTE\s+P[UÚ]BLICA\s+", "", punto, flags=re.IGNORECASE
    ).strip()

    # --- Palabras de ruido (no aportan a la geocodificación) ---
    punto = re.sub(
        r"\b(DISTRIBUC\w*|ABASTECIMIENTO|MUNICIPAL)\b",
        "",
        punto,
        flags=re.IGNORECASE,
    ).strip()
    punto = re.sub(
        r"\b(GRIFO|BARRA|ASEOS|ASEO|FP|FTE|ARU|NANÍN|PM|CGC|EDAR|PB|PN|RD|CONSUMIDOR)\b",
        "",
        punto,
        flags=re.IGNORECASE,
    ).strip()
    # Sufijo tipo código interno: -EEI, -D1A, etc.
    punto = re.sub(r"\s*-\s*[A-Z]{2,3}\d*$", "", punto).strip()
    # Company names anywhere in the string
    punto = re.sub(
        r"\b(EMACSA|AQUAGEST|GESTAGUA|APRESA|HIDROGESTI[OÓ]N)\s*(S\.?A\.?|S\.?L\.?)?\b",
        "",
        punto,
        flags=re.IGNORECASE,
    ).strip()

    # --- Mid-string "FUENTE" before street/landmark indicators ---
    punto = re.sub(
        r"\bFUENTE\s+(P[UÚ]BLICA\s+)?(PIL[OÓ]N\s+)?"
        r"(?=(C/|CALLE|PLAZA|PZA?\.?|PASEO|AVDA|AVENIDA|CTRA|CARRETERA|RÚA|Bº|BARRIO|PRAZA|PARQUE))",
        "", punto, flags=re.IGNORECASE
    ).strip()

    # --- Limpiar separadores sueltos y espacios ---
    punto = re.sub(r"^[-–/.,:\s]+|[-–/.,:\s]+$", "", punto).strip()
    punto = re.sub(r"\s{2,}", " ", punto).strip()
    # Trailing "DE", "DEL", "DE LA" (leftover from municipality removal)
    punto = re.sub(r"\s+(DE|DEL|DE\s+LA|DE\s+LOS|DE\s+LAS)\s*$", "", punto, flags=re.IGNORECASE).strip()
    # Paréntesis vacíos o sueltos
    punto = re.sub(r"\(\s*\)", "", punto).strip()
    punto = re.sub(r"^\(|\)$", "", punto).strip()

    # --- Deduplicar fragmentos repetidos (e.g., "COIRO- COIRO", "PRADO-PRADO X") ---
    parts = re.split(r"\s*[-–]\s*", punto)
    if len(parts) >= 2:
        seen = []
        for p in parts:
            p_clean = p.strip()
            if not p_clean:
                continue
            # Skip if this part is already contained in a previous part or vice versa
            is_dup = False
            for i, s in enumerate(seen):
                if p_clean.upper().startswith(s.upper()) or s.upper().startswith(p_clean.upper()):
                    # Keep the longer version
                    if len(p_clean) > len(s):
                        seen[i] = p_clean
                    is_dup = True
                    break
            if not is_dup:
                seen.append(p_clean)
        punto = " ".join(seen) if seen else ""

    # --- Descartar si el resultado es genérico o inútil ---
    if len(punto) < 3:
        return None

    # Accent-insensitive municipio comparison (only when municipio was removed)
    if remove_municipio and _strip_accents(punto).upper() == _strip_accents(municipio).upper():
        return None

    # Palabras que solas no sirven para geocodificar
    GENERIC_HINTS = {
        "AYUNTAMIENTO", "AYTO", "AYUNYAMIENTO",
        "FUENTE", "FUENTE PÚBLICA", "FUENTE PUBLICA",
        "OFICINAS", "OFICINA",
        "CASA CONSISTORIAL", "CONSULTORIO", "CONSULTORIO MEDICO", "CONSULTORIO MÉDICO",
        "CONSUMIDOR", "DE CONSUMIDOR", "DEL CONSUMIDOR",
        "CONTROL", "DEPOSITO", "DEPOSITO VIEJO", "DEPÓSITO",
        "BAR", "RESTAURANTE", "GASOLINERA",
        "LABORATORIO", "SERVICIOS",
        "ZONA PUEBLO", "ZONA DEPORTIVA", "ZONA ALTA", "ZONA BAJA",
        "PLANTA BAJA", "VESTUARIOS", "VESTUARIOS PLANTA BAJA",
        "CENTRO SALUD", "CENTRO DE SALUD",
        "POLIDEPORTIVO", "PISCINA", "PISCINA MUNICIPAL",
        "COLEGIO", "COLEGIO PUBLICO", "COLEGIO PÚBLICO",
        "CEMENTERIO", "CAMPO DE FUTBOL", "CAMPO DE FÚTBOL",
        "ENTRADA PUEBLO", "CENTRO PUEBLO",
        "JUNTO CATEDRAL", "JUNTO IGLESIA", "JUNTO AL AYUNTAMIENTO",
        "SALON ACTOS", "SALÓN DE ACTOS",
        "MATADERO", "MATADERO COMARCAL",
        "CENTRO INTERPRETACION", "CENTRO INTERPRETACIÓN",
        "FUENTE AYUNTAMIENTO", "FUENTE PLAZA",
        "FUENTE PARQUE", "FUENTE PARQUE INFANTIL",
        "PARQUE INFANTIL", "PARQUE",
        "IGLESIA", "NAVE", "NAVE INDUSTRIAL",
    }
    if punto.upper().strip() in GENERIC_HINTS:
        return None

    return punto


def _clean_light(punto: str) -> str:
    """
    Level B: light cleanup — strip PM/RED prefixes, company codes, province
    suffixes and ZA/parenthetical noise.  Keep municipality name, FUENTE, and
    all locality/address words intact.
    """
    punto = punto.replace("_", " ")
    # PM, PM1, E-PM, M-PM, PTMF, CP
    punto = re.sub(r"^(E-|M-)?PM\w?\d*\s*[-–.]?\s*", "", punto, flags=re.IGNORECASE).strip()
    punto = re.sub(r"^PTMF?\d*\s*[-–.]?\s*", "", punto, flags=re.IGNORECASE).strip()
    punto = re.sub(r"^CP\s*[-–.]?\s*", "", punto, flags=re.IGNORECASE).strip()
    # PTO. MUESTREO / PUNTO DE MUESTREO
    punto = re.sub(
        r"^(PTO\.?\s*MUESTREO|PUNTO\s+DE\s+MUESTREO)\s*[-–]?\s*",
        "", punto, flags=re.IGNORECASE,
    ).strip()
    # TORRE MUESTRA / ESTACIÓN TOMA DE MUESTRA
    punto = re.sub(
        r"^(TORRE\s+(TOMA\s+)?MUESTRA|ESTACI[OÓ]N\s+TOMA\s+DE\s+MUESTRA)\s*[-–.]?\s*",
        "", punto, flags=re.IGNORECASE,
    ).strip()
    # RED DE DISTRIBUCIÓN / RED DE ABASTECIMIENTO / RD at start
    punto = re.sub(
        r"^(RED\s+(DE\s+)?(DISTRIBUCIÓN|DISTRIBUCION|ABASTECIMIENTO)|RD)\s*[-–.,]?\s*",
        "", punto, flags=re.IGNORECASE,
    ).strip()
    # Company codes at start
    punto = re.sub(
        r"^(AQS|AQC|AQN|CGC|EMACSA|AQUAGEST\s*\w*|GESTAGUA|APRESA|HIDROGESTI[OÓ]N\s*S\.?A\.?)\s*[-–,]?\s*",
        "", punto, flags=re.IGNORECASE,
    ).strip()
    # ECC-2, ECC-3
    punto = re.sub(r"^ECC-?\d+\s*[-–.]?\s*", "", punto, flags=re.IGNORECASE).strip()
    # AYTO, TUT, EST. prefix
    punto = re.sub(r"^(AYTO\.?|TUT|EST\.?)\s*[-–]?\s*", "", punto, flags=re.IGNORECASE).strip()
    # /ZA, -ZA suffix
    punto = re.sub(r"\s*[-–/(,\s]\s*(ZA|ZONA\s+ABAST)\b.*$", "", punto, flags=re.IGNORECASE).strip()
    # Province code suffix: -CR, -CU-, -AB
    punto = re.sub(r"\s*[-–,]\s*[A-Z]{1,2}\s*[-–]?\s*$", "", punto).strip()
    # Parenthetical RED/ABASTECIMIENTO
    punto = re.sub(r"\(.*?(RED|ABASTECIMIENTO|DISTRIBUC).*?\)", "", punto, flags=re.IGNORECASE).strip()
    # Internal code suffix: -EEI, -D1A
    punto = re.sub(r"\s*-\s*[A-Z]{2,3}\d*$", "", punto).strip()
    # Leading number/code: "02 ", "5629# "
    punto = re.sub(r"^\d{1,5}#?\s+", "", punto).strip()
    punto = re.sub(r"^F/S\s+\d[\d/]+\s+\w{2,4}\s+\d+\.?\s*", "", punto, flags=re.IGNORECASE).strip()
    # Clean separators and spaces
    punto = re.sub(r"^[-–/.,:\s]+|[-–/.,:\s]+$", "", punto).strip()
    punto = re.sub(r"\s{2,}", " ", punto).strip()
    return punto


def _format_queries(
    text: str, municipio: str, provincia: str
) -> tuple[list[str], list[str]]:
    """Format cleaned text into queries for standard providers and CartoCiudad.

    Returns (standard_queries, carto_queries) where:
      - standard_queries: for Photon/Nominatim (include "España")
      - carto_queries: for CartoCiudad (NO "España", NO provincia — it chokes on them)
    """
    if not text or len(text) < 3:
        return [], []
    std = [f"{text}, {municipio}, España"]
    if provincia:
        std.append(f"{text}, {municipio}, {provincia}, España")
    carto = [f"{text}, {municipio}", text]
    return std, carto


def build_query_levels(
    punto: str, municipio: str, provincia: str
) -> list[tuple[str, list[str], list[str]]]:
    """
    Build query levels with incremental cleaning (matrix-validated).

    Based on matrix test results:
    - All cleaning steps help EXCEPT municipio removal (hurts all providers)
    - Biggest impact steps: RED removal, PM removal, FUENTE removal

    Returns list of (level_name, standard_queries, carto_queries) tuples.
    """
    levels: list[tuple[str, list[str], list[str]]] = []

    # --- Level A: raw (just normalize underscores/whitespace) ---
    raw = punto.replace("_", " ").strip()
    raw = re.sub(r"\s{2,}", " ", raw)
    std_a, carto_a = _format_queries(raw, municipio, provincia)
    if std_a:
        levels.append(("A", std_a, carto_a))

    # --- Level B: light clean (strip prefixes/suffixes, keep municipio) ---
    light = _clean_light(punto)
    if light and light.upper() != raw.upper():
        std_b, carto_b = _format_queries(light, municipio, provincia)
        if std_b:
            levels.append(("B", std_b, carto_b))

    # --- Level C: deep clean (all steps EXCEPT municipio removal) ---
    deep = extract_location_hint(punto, municipio, remove_municipio=False)
    if deep and deep.upper() != light.upper():
        std_c, carto_c = _format_queries(deep, municipio, provincia)
        if std_c:
            levels.append(("C", std_c, carto_c))

    return levels


def build_geocode_queries(hint: str, municipio: str, provincia: str) -> list[str]:
    """Construye una lista priorizada de queries para Nominatim (legacy)."""
    queries = []

    has_street = re.search(
        r"\b(C/|Calle|Plaza|Pz[aA]?\.?|Avda\.?|Avenida|Ctra\.?|Carretera|Paseo|"
        r"Travesía|Rúa|Bº|Barrio|Font|Plaça|Praza)\b",
        hint,
        re.IGNORECASE,
    )

    hint_clean = hint
    if has_street:
        hint_clean = re.sub(
            r"^FUENTE\s+(P[UÚ]BLICA\s+)?(PIL[OÓ]N\s+)?",
            "", hint, flags=re.IGNORECASE
        ).strip() or hint

    if has_street:
        queries.append(f"{hint_clean}, {municipio}, España")
        queries.append(f"{hint_clean}, {municipio}, {provincia}, España")
    else:
        queries.append(f"{hint}, {municipio}, {provincia}, España")
        if len(hint.split()) <= 3:
            queries.append(f"{hint}, {provincia}, España")

    return queries


def nominatim_geocode(
    geolocator: Nominatim, query: str
) -> tuple[float, float] | None:
    """Geocodifica una query con Nominatim, con reintentos."""
    for attempt in range(MAX_RETRIES):
        try:
            time.sleep(NOMINATIM_DELAY)
            loc = geolocator.geocode(query, timeout=10)
            if loc:
                return (loc.latitude, loc.longitude)
            return None
        except (GeocoderTimedOut, GeocoderServiceError):
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            continue
    return None


# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------

def load_province_names() -> dict[str, str]:
    """Carga el mapeo provincia_code -> provincia_name desde el catálogo."""
    catalog = pd.read_csv(CATALOG_PATH, dtype=str)
    mapping = {}
    for _, row in catalog.drop_duplicates("provincia_code").iterrows():
        name = row["provincia_name"]
        name = re.sub(r"^\d+\s*", "", name).strip()
        mapping[row["provincia_code"]] = name
    return mapping


def load_measurement_points() -> pd.DataFrame:
    """Lee todos los CSVs y devuelve los puntos de muestreo únicos."""
    files = sorted(CSV_DIR.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No se encontraron CSVs en {CSV_DIR}")

    frames = []
    for f in files:
        df = pd.read_csv(
            f,
            dtype=str,
            usecols=["provincia_code", "municipio_code", "municipio", "punto_muestreo"],
        )
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)
    puntos = combined.drop_duplicates(
        subset=["provincia_code", "municipio_code", "punto_muestreo"]
    ).reset_index(drop=True)

    print(f"  Puntos de muestreo únicos: {len(puntos)}")
    return puntos


def load_municipality_centroids() -> dict[tuple[str, str], tuple[float, float]]:
    """Carga los centroides de municipios ya geocodificados."""
    if not MUNI_GEOCODE_CACHE.exists():
        return {}

    df = pd.read_csv(MUNI_GEOCODE_CACHE, dtype={"provincia_code": str, "municipio_code": str})
    centroids = {}
    for _, row in df.iterrows():
        if pd.notna(row.get("lat_municipio")) and pd.notna(row.get("lon_municipio")):
            key = (row["provincia_code"], row["municipio_code"])
            centroids[key] = (float(row["lat_municipio"]), float(row["lon_municipio"]))
    return centroids


def save_municipality_centroids(
    centroids: dict[tuple[str, str], tuple[float, float]],
    muni_names: dict[tuple[str, str], str],
) -> None:
    """Guarda los centroides de municipios al cache CSV."""
    rows = []
    for (prov, muni), (lat, lon) in sorted(centroids.items()):
        rows.append({
            "provincia_code": prov,
            "municipio_code": muni,
            "municipio": muni_names.get((prov, muni), ""),
            "lat_municipio": lat,
            "lon_municipio": lon,
        })
    df = pd.DataFrame(rows)
    df.to_csv(MUNI_GEOCODE_CACHE, index=False)


def load_fine_cache() -> dict[str, dict]:
    """Carga el cache de geocodificación fina (punto_muestreo)."""
    if FINE_GEOCODE_CACHE.exists():
        with open(FINE_GEOCODE_CACHE) as f:
            return json.load(f)
    return {}


def save_fine_cache(cache: dict[str, dict]) -> None:
    """Guarda el cache de geocodificación fina."""
    with open(FINE_GEOCODE_CACHE, "w") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Geocodificación fina de un punto
# ---------------------------------------------------------------------------

def geocode_punto(
    geolocator: Nominatim,
    punto: str,
    municipio: str,
    provincia: str,
    centroid: tuple[float, float],
) -> dict:
    """
    Intenta geocodificar un punto de muestreo con múltiples niveles de
    limpieza (A→B→C), cada uno probando todos los proveedores.
    Devuelve un dict con lat, lon, source.
    """
    levels = build_query_levels(punto, municipio, provincia)
    if not levels:
        return {"lat": centroid[0], "lon": centroid[1], "source": "centroide"}

    result = geocode_multi(geolocator, levels, centroid)
    if result:
        return {
            "lat": round(result[0], 7),
            "lon": round(result[1], 7),
            "source": f"geocodificado ({result[2]}/{result[3]})",
        }

    return {"lat": centroid[0], "lon": centroid[1], "source": "centroide"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Geocodificar puntos de muestreo")
    parser.add_argument(
        "--max-points",
        type=int,
        default=0,
        help="Limitar el nº de puntos a geocodificar en la pasada fina (0 = todos)",
    )
    parser.add_argument(
        "--skip-fine",
        action="store_true",
        help="Omitir la geocodificación fina (solo centroides)",
    )
    args = parser.parse_args()

    print("=== Geocodificación de puntos de muestreo ===\n")

    # -----------------------------------------------------------------------
    # 1. Cargar datos
    # -----------------------------------------------------------------------
    print("[1/4] Cargando datos...")
    puntos = load_measurement_points()
    centroids = load_municipality_centroids()
    province_names = load_province_names()
    print(f"  Centroides ya en caché: {len(centroids)}")

    # -----------------------------------------------------------------------
    # 2. Geocodificar municipios que falten
    # -----------------------------------------------------------------------
    print("\n[2/4] Geocodificando centroides de municipios...")

    # Obtener municipios únicos del dataset
    unique_munis = (
        puntos[["provincia_code", "municipio_code", "municipio"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    muni_names: dict[tuple[str, str], str] = {}
    for _, row in unique_munis.iterrows():
        muni_names[(row["provincia_code"], row["municipio_code"])] = row["municipio"]

    # Añadir los ya cacheados al dict de nombres
    if MUNI_GEOCODE_CACHE.exists():
        cached_df = pd.read_csv(
            MUNI_GEOCODE_CACHE,
            dtype={"provincia_code": str, "municipio_code": str},
        )
        for _, row in cached_df.iterrows():
            k = (row["provincia_code"], row["municipio_code"])
            if k not in muni_names:
                muni_names[k] = row.get("municipio", "")

    # Encontrar los que faltan
    todo_munis = [
        (row["provincia_code"], row["municipio_code"], row["municipio"])
        for _, row in unique_munis.iterrows()
        if (row["provincia_code"], row["municipio_code"]) not in centroids
    ]

    if todo_munis:
        print(f"  Municipios por geocodificar: {len(todo_munis)}")
        est_min = len(todo_munis) * NOMINATIM_DELAY / 60
        print(f"  Tiempo estimado: ~{est_min:.0f} minutos")

        geolocator = Nominatim(user_agent="calidad-agua-geocode-puntos/1.0")
        geocoded_ok = 0
        geocoded_fail = 0

        for i, (prov, muni_code, muni_name) in enumerate(todo_munis):
            prov_name = province_names.get(prov, "")
            query = f"{muni_name}, {prov_name}, España"
            coords = nominatim_geocode(geolocator, query)

            if coords:
                centroids[(prov, muni_code)] = coords
                geocoded_ok += 1
            else:
                geocoded_fail += 1

            if (i + 1) % 25 == 0 or (i + 1) == len(todo_munis):
                print(f"  Progreso municipios: {i + 1}/{len(todo_munis)} "
                      f"(OK: {geocoded_ok}, fallos: {geocoded_fail})")
                save_municipality_centroids(centroids, muni_names)

        save_municipality_centroids(centroids, muni_names)
        print(f"  Municipios geocodificados: {geocoded_ok}, fallos: {geocoded_fail}")
    else:
        print("  Todos los municipios ya están en caché.")
        geolocator = Nominatim(user_agent="calidad-agua-geocode-puntos/1.0")

    # Asignar centroides a todos los puntos
    results: list[dict] = []
    missing_centroid = 0
    for _, row in puntos.iterrows():
        key = (row["provincia_code"], row["municipio_code"])
        centroid = centroids.get(key)
        if centroid is None:
            missing_centroid += 1
        results.append({
            "provincia_code": row["provincia_code"],
            "municipio_code": row["municipio_code"],
            "municipio": row["municipio"],
            "punto_muestreo": row["punto_muestreo"],
            "lat": round(centroid[0], 7) if centroid else None,
            "lon": round(centroid[1], 7) if centroid else None,
            "source": "centroide",
        })

    if missing_centroid:
        print(f"  AVISO: {missing_centroid} puntos sin centroide")
    print(f"  {len(results)} puntos con centroide asignado")

    # -----------------------------------------------------------------------
    # 3. Pasada fina: geocodificación por nombre del punto
    # -----------------------------------------------------------------------
    if not args.skip_fine:
        print("\n[3/4] Geocodificación fina de puntos de muestreo...")
        fine_cache = load_fine_cache()
        total = len(results)
        if args.max_points > 0:
            total = min(total, args.max_points)

        geocoded_count = 0
        cached_count = 0
        skipped_no_hint = 0
        api_calls = 0

        for i, entry in enumerate(results[:total]):
            cache_key = (
                f"{entry['provincia_code']}_{entry['municipio_code']}"
                f"_{entry['punto_muestreo']}"
            )

            # Consultar cache
            if cache_key in fine_cache:
                cached = fine_cache[cache_key]
                entry["lat"] = cached["lat"]
                entry["lon"] = cached["lon"]
                entry["source"] = cached["source"]
                cached_count += 1
                if cached["source"].startswith("geocodificado"):
                    geocoded_count += 1
                continue

            centroid_coords = centroids.get(
                (entry["provincia_code"], entry["municipio_code"])
            )
            if centroid_coords is None:
                fine_cache[cache_key] = {
                    "lat": entry["lat"],
                    "lon": entry["lon"],
                    "source": "centroide",
                }
                continue

            # Comprobar si hay queries útiles antes de llamar a la API
            prov_name = province_names.get(entry["provincia_code"], "")
            levels = build_query_levels(
                entry["punto_muestreo"], entry["municipio"], prov_name
            )
            if not levels:
                fine_cache[cache_key] = {
                    "lat": centroid_coords[0],
                    "lon": centroid_coords[1],
                    "source": "centroide",
                }
                skipped_no_hint += 1
                continue
            result = geocode_punto(
                geolocator,
                entry["punto_muestreo"],
                entry["municipio"],
                prov_name,
                centroid_coords,
            )

            entry["lat"] = result["lat"]
            entry["lon"] = result["lon"]
            entry["source"] = result["source"]
            api_calls += 1

            fine_cache[cache_key] = {
                "lat": result["lat"],
                "lon": result["lon"],
                "source": result["source"],
            }

            if result["source"].startswith("geocodificado"):
                geocoded_count += 1

            # Progreso cada 50 puntos procesados
            if (i + 1) % 50 == 0 or (i + 1) == total:
                pct = (i + 1) / total * 100
                print(
                    f"  Progreso: {i + 1}/{total} ({pct:.0f}%) "
                    f"— geocodificados: {geocoded_count}, "
                    f"caché: {cached_count}, "
                    f"sin pista: {skipped_no_hint}, "
                    f"API calls: {api_calls}"
                )
                save_fine_cache(fine_cache)

        save_fine_cache(fine_cache)
        print(f"\n  Resumen geocodificación fina:")
        print(f"    Geocodificados con dirección: {geocoded_count}")
        print(f"    Recuperados de caché:          {cached_count}")
        print(f"    Sin pista de ubicación:        {skipped_no_hint}")
        print(f"    Llamadas a API:                {api_calls}")
    else:
        print("\n[3/4] Geocodificación fina omitida (--skip-fine)")

    # -----------------------------------------------------------------------
    # 4. Generar YAML
    # -----------------------------------------------------------------------
    print("\n[4/4] Generando YAML...")

    by_municipio: dict[str, dict] = {}
    for entry in results:
        muni_key = f"{entry['provincia_code']}_{entry['municipio_code']}"
        if muni_key not in by_municipio:
            centroid = centroids.get(
                (entry["provincia_code"], entry["municipio_code"])
            )
            by_municipio[muni_key] = {
                "provincia_code": entry["provincia_code"],
                "municipio_code": entry["municipio_code"],
                "municipio": entry["municipio"],
                "centroide": {
                    "lat": round(centroid[0], 7) if centroid else None,
                    "lon": round(centroid[1], 7) if centroid else None,
                },
                "puntos_muestreo": [],
            }
        by_municipio[muni_key]["puntos_muestreo"].append({
            "nombre": entry["punto_muestreo"],
            "lat": entry["lat"],
            "lon": entry["lon"],
            "source": entry["source"],
        })

    output = {"municipios": list(by_municipio.values())}

    with open(OUTPUT_YAML, "w", encoding="utf-8") as f:
        yaml.dump(
            output,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )

    n_geocoded = sum(1 for e in results if e["source"].startswith("geocodificado"))
    n_centroid = sum(1 for e in results if e.get("source") == "centroide")
    n_null = sum(1 for e in results if e["lat"] is None)
    print(f"\n  Archivo generado: {OUTPUT_YAML}")
    print(f"  Municipios: {len(by_municipio)}")
    print(f"  Puntos totales: {len(results)}")
    print(f"    - Con coordenada fina: {n_geocoded}")
    print(f"    - Con centroide:       {n_centroid}")
    if n_null:
        print(f"    - Sin coordenadas:     {n_null}")
    print("\n=== Listo ===")


if __name__ == "__main__":
    main()
