#!/usr/bin/env python3
"""Descarga el nomenclátor (gazetteer) offline de entidades de población de España.

Fuente: GeoNames — https://download.geonames.org/export/dump/
Licencia: Creative Commons Attribution 4.0 (CC-BY 4.0).
          Atribución requerida: "GeoNames.org".

Ficheros descargados a data/geo/gazetteer/:
  - ES.txt                 dump completo de España (todas las feature classes)
  - alternateNamesES.txt    nombres alternativos (variantes gallega/catalana/euskera)
  - admin1CodesASCII.txt    códigos admin1 (comunidades autónomas)
  - admin2Codes.txt         códigos admin2 (provincias)
  - gazetteer_places.csv    DERIVADO: sólo feature class P (lugares poblados) +
                            ADM3/ADM4, con provincia INE resuelta.
  - gazetteer_capitals.csv  DERIVADO: capitales municipales (PPLA*/PPLC) y
                            núcleos principales por población.

Sobre el NGBE / NGMEP del IGN (CNIG):
  El Centro de Descargas del CNIG (centrodedescargas.cnig.es) sirve los ficheros
  detrás de un formulario con sesión/token, no por URL estable. La URL que se
  suele citar, https://centrodedescargas.cnig.es/CentroDescargas/documentos/NGBE.zip,
  devuelve 404. Ver NGBE_NOTES en este fichero para lo que se probó.
  Por eso la fuente primaria aquí es GeoNames.

Uso:
    python scripts/download_gazetteer.py [--force]
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import unicodedata
import zipfile
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "data" / "geo" / "gazetteer"

GEONAMES_BASE = "https://download.geonames.org/export/dump"

# (url, nombre del fichero dentro del zip o None si no es zip, nombre final)
DOWNLOADS = [
    (f"{GEONAMES_BASE}/ES.zip", "ES.txt", "ES.txt"),
    (f"{GEONAMES_BASE}/alternatenames/ES.zip", "ES.txt", "alternateNamesES.txt"),
    (f"{GEONAMES_BASE}/admin1CodesASCII.txt", None, "admin1CodesASCII.txt"),
    (f"{GEONAMES_BASE}/admin2Codes.txt", None, "admin2Codes.txt"),
    (f"{GEONAMES_BASE}/featureCodes_en.txt", None, "featureCodes_en.txt"),
]

# Columnas del dump geoNames (ver readme.txt de GeoNames)
GN_COLS = [
    "geonameid", "name", "asciiname", "alternatenames", "latitude", "longitude",
    "feature_class", "feature_code", "country_code", "cc2", "admin1_code",
    "admin2_code", "admin3_code", "admin4_code", "population", "elevation",
    "dem", "timezone", "modification_date",
]

NGBE_NOTES = """
Intentos con el CNIG (todos fallidos, documentados para no repetirlos):
  - https://centrodedescargas.cnig.es/CentroDescargas/documentos/NGBE.zip        -> 404
  - https://centrodedescargas.cnig.es/CentroDescargas/documentos/NGMEP.zip       -> 404
  - .../CentroDescargas/descargaDir (POST del formulario)                        -> requiere
    secuencia de sesión + token CSRF obtenidos navegando el buscador; no es una
    URL estable y rompería en CI.
El NGBE sería más autoritativo (incluye punto de capital municipal oficial y tipo
de entidad EATIM/parroquia/lugar), pero no es scriptable de forma fiable.
"""

# ---------------------------------------------------------------------------
# Mapeo GeoNames admin2 -> código de provincia INE
# ---------------------------------------------------------------------------
# Los códigos admin2 de GeoNames para España NO son códigos INE. Se resuelven
# por el NOMBRE de la provincia que aparece en admin2Codes.txt, normalizado.
# Esta tabla mapea el nombre normalizado de provincia -> código INE de 2 dígitos.
INE_PROVINCIAS = {
    "araba/alava": "01", "alava": "01", "araba": "01",
    "albacete": "02",
    "alicante": "03", "alacant": "03", "alicante/alacant": "03",
    "almeria": "04",
    "avila": "05",
    "badajoz": "06",
    "baleares": "07", "illes balears": "07", "islas baleares": "07",
    "balearic islands": "07", "les illes balears": "07",
    "barcelona": "08",
    "burgos": "09",
    "caceres": "10",
    "cadiz": "11",
    "castellon": "12", "castello": "12", "castellon/castello": "12",
    "ciudad real": "13",
    "cordoba": "14",
    "a coruna": "15", "la coruna": "15", "coruna": "15", "a coruna/la coruna": "15",
    "cuenca": "16",
    "girona": "17", "gerona": "17",
    "granada": "18",
    "guadalajara": "19",
    "gipuzkoa": "20", "guipuzcoa": "20",
    "huelva": "21",
    "huesca": "22",
    "jaen": "23",
    "leon": "24",
    "lleida": "25", "lerida": "25",
    "la rioja": "26", "rioja": "26",
    "lugo": "27",
    "madrid": "28",
    "malaga": "29",
    "murcia": "30",
    "navarra": "31", "nafarroa": "31", "navarre": "31",
    "ourense": "32", "orense": "32",
    "asturias": "33",
    "palencia": "34",
    "las palmas": "35", "palmas": "35",
    "pontevedra": "36",
    "salamanca": "37",
    "santa cruz de tenerife": "38", "tenerife": "38",
    "cantabria": "39",
    "segovia": "40",
    "sevilla": "41",
    "soria": "42",
    "tarragona": "43",
    "teruel": "44",
    "toledo": "45",
    "valencia": "46", "valencia/valencia": "46", "valencia/valencia": "46",
    "valladolid": "47",
    "bizkaia": "48", "vizcaya": "48",
    "zamora": "49",
    "zaragoza": "50",
    "ceuta": "51",
    "melilla": "52",
}


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def norm(s: str) -> str:
    return strip_accents(s or "").lower().strip()


def download(url: str, member: str | None, dest: Path, force: bool) -> None:
    if dest.exists() and not force:
        print(f"  [skip] {dest.name} ya existe ({dest.stat().st_size:,} bytes)")
        return
    print(f"  bajando {url} ...", end=" ", flush=True)
    r = requests.get(url, timeout=180, headers={"User-Agent": "calidad-agua-gazetteer/1.0"})
    r.raise_for_status()
    if member:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            data = z.read(member)
    else:
        data = r.content
    dest.write_bytes(data)
    print(f"ok ({len(data):,} bytes) -> {dest.name}")


def load_admin2_to_ine(path: Path) -> tuple[dict[str, str], list[str]]:
    """admin2Codes.txt: 'ES.<a1>.<a2>\tnombre\tnombre_ascii\tgeonameid'.

    Devuelve {"<a1>.<a2>": ine_prov} y la lista de nombres no resueltos.
    """
    mapping: dict[str, str] = {}
    unresolved: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2 or not parts[0].startswith("ES."):
                continue
            key = parts[0][3:]  # quita "ES."
            name = parts[1]
            n = norm(name)
            # nombres tipo "Provincia de Sevilla" / "Provincia da Coruña" /
            # "Sevilla Province" / "Probintzia"
            for pref in ("provincia de ", "provincia da ", "provincia del ",
                         "provincia d'", "provincia ", "province of ",
                         "provincia de ", "provincia "):
                if n.startswith(pref):
                    n = n[len(pref):]
                    break
            for suf in (" province", " provincia"):
                if n.endswith(suf):
                    n = n[: -len(suf)]
            ine = INE_PROVINCIAS.get(n)
            if ine is None:
                # intenta la primera mitad de nombres bilingües "X/Y"
                if "/" in n:
                    ine = INE_PROVINCIAS.get(n.split("/")[0].strip())
            if ine is None:
                unresolved.append(f"{parts[0]}\t{name}")
            else:
                mapping[key] = ine
    return mapping, unresolved


def build_derived(out_dir: Path) -> None:
    """Genera gazetteer_places.csv y gazetteer_capitals.csv."""
    a2_map, unresolved = load_admin2_to_ine(out_dir / "admin2Codes.txt")
    print(f"\n  admin2 ES resueltos a provincia INE: {len(a2_map)}")
    if unresolved:
        print(f"  !! admin2 SIN resolver ({len(unresolved)}):")
        for u in unresolved:
            print(f"       {u}")

    # feature codes que nos interesan como "entidad de población"
    PLACE_CODES = {
        "PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC", "PPLF",
        "PPLL", "PPLQ", "PPLR", "PPLS", "PPLW", "PPLX", "STLMT",
    }
    CAPITAL_CODES = {"PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC"}

    src = out_dir / "ES.txt"
    places_path = out_dir / "gazetteer_places.csv"
    caps_path = out_dir / "gazetteer_capitals.csv"

    n_in = n_place = n_noprov = 0
    rows = []
    with src.open(encoding="utf-8") as fh:
        rd = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        for parts in rd:
            if len(parts) < 19:
                continue
            n_in += 1
            rec = dict(zip(GN_COLS, parts))
            fcode = rec["feature_code"]
            fclass = rec["feature_class"]
            keep_place = fclass == "P" and fcode in PLACE_CODES
            keep_adm = fclass == "A" and fcode in {"ADM3", "ADM4"}
            if not (keep_place or keep_adm):
                continue
            a2key = f"{rec['admin1_code']}.{rec['admin2_code']}"
            ine = a2_map.get(a2key, "")
            if not ine:
                n_noprov += 1
            n_place += 1
            rows.append({
                "geonameid": rec["geonameid"],
                "name": rec["name"],
                "asciiname": rec["asciiname"],
                "alternatenames": rec["alternatenames"],
                "lat": rec["latitude"],
                "lon": rec["longitude"],
                "feature_class": fclass,
                "feature_code": fcode,
                "admin1": rec["admin1_code"],
                "admin2": rec["admin2_code"],
                "admin3": rec["admin3_code"],
                "admin4": rec["admin4_code"],
                "provincia_ine": ine,
                "population": rec["population"] or "0",
            })

    fields = list(rows[0].keys())
    with places_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  {places_path.name}: {n_place:,} entidades "
          f"(de {n_in:,} registros; {n_noprov:,} sin provincia INE)")

    caps = [r for r in rows if r["feature_code"] in CAPITAL_CODES]
    with caps_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(caps)
    print(f"  {caps_path.name}: {len(caps):,} capitales administrativas")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-descarga aunque exista")
    ap.add_argument("--derive-only", action="store_true",
                    help="no descarga, sólo regenera los CSV derivados")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"gazetteer -> {OUT_DIR}")

    if not args.derive_only:
        for url, member, name in DOWNLOADS:
            download(url, member, OUT_DIR / name, args.force)

    (OUT_DIR / "LICENSE.txt").write_text(
        "Datos de GeoNames (https://www.geonames.org/).\n"
        "Licencia: Creative Commons Attribution 4.0 (CC-BY 4.0).\n"
        "Atribución requerida: GeoNames.org\n"
        f"Descargado de {GEONAMES_BASE}/ES.zip\n"
        "\n"
        "Nota sobre el NGBE/NGMEP del IGN:\n" + NGBE_NOTES,
        encoding="utf-8",
    )

    build_derived(OUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
