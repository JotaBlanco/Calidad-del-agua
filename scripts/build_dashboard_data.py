"""
build_dashboard_data.py — Generate static JSON files for the GitHub Pages dashboard.

Runs the ETL pipeline (s0 through s5) on all_data.csv, filters to the latest
measurements per sampling point, joins coordinates, and exports:
  - provincia/{code}.json       — latest measurements with dictionary encoding
  - provincia/{code}_hist.json  — historical time series per punto × param

Usage:
    python -m scripts.build_dashboard_data
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from scripts.etl import (
    ALL_DATA_FILE,
    DATA_DIR,
    PROCESSED_DIR,
    clean_types,
    merge_with_limits,
    add_compliance,
    tag_latest,
)

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DATA_DIR = ROOT / "docs" / "dashboard" / "data"

LOCATIONS_CATALOG = DATA_DIR / "locations_catalog.csv"
PUNTOS_COORDS_YAML = PROCESSED_DIR / "puntos_coordenadas.yaml"
GEOCODED_MUNIS = PROCESSED_DIR / "geocoded_municipalities.csv"

# Aptitud encoding: 0 = apta, 1 = incumple_vp, 2 = no_apta
APTITUD_CODE = {"apta": 0, "incumple_vp": 1, "no_apta": 2}

# Range-based parameters with special limit handling
RANGE_PARAMS = {
    "pH": {"vp": [6.5, 9.5], "vna": [4.5, 10.0]},
    "Índice de Langelier": {"vp": [-0.5, 0.5], "vna": None},
}


# ── Coordinate loading ──────────────────────────────────────────────


def load_punto_coords() -> dict[tuple[str, str], dict]:
    """Load punto-level coordinates from puntos_coordenadas.yaml."""
    coords: dict[tuple[str, str], dict] = {}
    with open(PUNTOS_COORDS_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for muni in data.get("municipios", []):
        muni_code = str(muni["municipio_code"])
        for punto in muni.get("puntos_muestreo", []):
            lat, lon = punto.get("lat"), punto.get("lon")
            if lat and lon and not (lat == 0 and lon == 0):
                coords[(muni_code, punto["nombre"])] = {
                    "lat": round(float(lat), 6),
                    "lon": round(float(lon), 6),
                }
    return coords


def load_muni_centroids() -> dict[str, dict]:
    """Load municipality centroids as fallback coordinates."""
    df = pd.read_csv(GEOCODED_MUNIS, dtype=str)
    centroids: dict[str, dict] = {}
    for _, row in df.iterrows():
        lat, lon = row.get("lat_municipio"), row.get("lon_municipio")
        if pd.notna(lat) and pd.notna(lon):
            centroids[str(row["municipio_code"])] = {
                "lat": round(float(lat), 6),
                "lon": round(float(lon), 6),
            }
    return centroids


def load_location_names() -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Load province and municipality names from the locations catalog."""
    df = pd.read_csv(LOCATIONS_CATALOG, dtype=str)
    prov_names: dict[str, str] = {}
    muni_names: dict[str, tuple[str, str]] = {}
    for _, row in df.iterrows():
        pcode = str(row["provincia_code"]).lstrip("0") or "0"
        pname = str(row.get("provincia_name", "")).strip()
        if pname and pname[0].isdigit():
            parts = pname.split(" ", 1)
            pname = parts[1] if len(parts) > 1 else pname
        prov_names[pcode] = pname
        mcode = str(row["municipio_code"])
        mname = str(row.get("municipio_name", "")).strip()
        muni_names[mcode] = (mname, pcode)
    return prov_names, muni_names


# ── Dictionary building ─────────────────────────────────────────────


def build_param_dict(prov_df: pd.DataFrame) -> tuple[list, dict[str, int]]:
    """Build a parameter dictionary for a provincia.

    Returns:
        dict_entries: list of [name, unit, limite, vna, parte]
            - limite: number | [lo, hi] for range params
            - vna: number | [lo, hi] | null
        param_to_idx: {parametro_boe → index}
    """
    # Get unique (parametro_boe, unidad, valor_parametrico, valor_no_aptitud, parte_parametro)
    assessed = prov_df[prov_df["aptitud"].notna()].copy()
    if assessed.empty:
        return [], {}

    params = (
        assessed.groupby("parametro_boe", sort=True)
        .first()[["unidad", "valor_parametrico", "valor_no_aptitud", "parte_parametro"]]
        .reset_index()
    )

    dict_entries = []
    param_to_idx: dict[str, int] = {}

    for idx, row in params.iterrows():
        name = str(row["parametro_boe"])

        # Handle range params specially
        if name in RANGE_PARAMS:
            rp = RANGE_PARAMS[name]
            limite = rp["vp"]
            vna = rp["vna"]
        else:
            limite = _safe_num(row["valor_parametrico"])
            vna = _safe_num(row["valor_no_aptitud"])

        entry = [
            name,
            str(row["unidad"]) if pd.notna(row["unidad"]) else "",
            limite,
            vna,
            str(row["parte_parametro"]) if pd.notna(row["parte_parametro"]) else "",
        ]
        param_to_idx[name] = len(dict_entries)
        dict_entries.append(entry)

    return dict_entries, param_to_idx


def build_date_pool(prov_df: pd.DataFrame) -> tuple[list[str], dict[str, int]]:
    """Build a date string pool for a provincia."""
    dates_series = prov_df["fecha"].dropna().dt.strftime("%Y-%m-%d")
    unique_dates = sorted(dates_series.unique())
    date_to_idx = {d: i for i, d in enumerate(unique_dates)}
    return unique_dates, date_to_idx


# ── Helpers ─────────────────────────────────────────────────────────


def _safe_num(val) -> float | None:
    """Convert a value to float, returning None for NaN/non-numeric."""
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None


def compute_bounds(lats: list[float], lons: list[float]) -> list[list[float]] | None:
    """Compute [[min_lat, min_lon], [max_lat, max_lon]] bounding box."""
    if not lats or not lons:
        return None
    return [
        [round(min(lats), 4), round(min(lons), 4)],
        [round(max(lats), 4), round(max(lons), 4)],
    ]


# ── Provincia JSON building ─────────────────────────────────────────


def build_provincia_json(
    prov_code: str,
    prov_name: str,
    prov_latest: pd.DataFrame,
    param_to_idx: dict[str, int],
    date_pool: list[str],
    date_to_idx: dict[str, int],
    dict_entries: list,
    punto_coords: dict,
    muni_centroids: dict,
    muni_names: dict,
) -> dict:
    """Build the main JSON structure for a provincia with dictionary encoding."""
    municipios_out = []
    all_lats = []
    all_lons = []

    for muni_code, muni_group in prov_latest.groupby("municipio_code", sort=True):
        muni_code_str = str(muni_code)
        muni_name_info = muni_names.get(muni_code_str)
        muni_name = muni_name_info[0] if muni_name_info else muni_group["municipio"].iloc[0]

        muni_centroid = muni_centroids.get(muni_code_str, {})
        muni_lat = muni_centroid.get("lat")
        muni_lon = muni_centroid.get("lon")

        puntos_out = []
        for punto_nombre, punto_group in muni_group.groupby("punto_muestreo", sort=True):
            # Coordinates
            punto_coord = punto_coords.get((muni_code_str, punto_nombre))
            if punto_coord:
                lat, lon = punto_coord["lat"], punto_coord["lon"]
            elif muni_lat and muni_lon:
                lat, lon = muni_lat, muni_lon
            else:
                lat, lon = None, None

            if lat is not None:
                all_lats.append(lat)
                all_lons.append(lon)

            # Build compact measurements: [dict_idx, valor, aptitud_code, date_idx]
            measurements = []
            has_danger = False
            has_warning = False
            n_apta = 0
            calificacion_no_apta = False

            for _, row in punto_group.iterrows():
                aptitud = row.get("aptitud")
                param_boe = row.get("parametro_boe")

                if pd.isna(aptitud) or param_boe not in param_to_idx:
                    continue

                pidx = param_to_idx[param_boe]
                valor = _safe_num(row.get("valor"))
                acode = APTITUD_CODE.get(str(aptitud), -1)
                fecha_str = row["fecha"].strftime("%Y-%m-%d") if pd.notna(row.get("fecha")) else None
                didx = date_to_idx.get(fecha_str, -1) if fecha_str else -1

                measurements.append([pidx, valor, acode, didx])

                if acode == 2:
                    has_danger = True
                elif acode == 1:
                    has_warning = True
                else:
                    n_apta += 1

                if str(row.get("calificacion", "")) == "AGUA NO APTA PARA EL CONSUMO":
                    calificacion_no_apta = True

            # Sort: no_apta (2) first, then incumple (1), then apta (0)
            measurements.sort(key=lambda m: (-m[2], m[0]))

            # Determine overall status
            if has_danger:
                status = "danger"
            elif has_warning:
                status = "warning"
            elif calificacion_no_apta:
                status = "warning"
            else:
                status = "ok"

            punto_data = {
                "nombre": str(punto_nombre),
                "lat": lat,
                "lon": lon,
                "status": status,
                "m": measurements,
            }
            puntos_out.append(punto_data)

        statuses = [p["status"] for p in puntos_out]
        municipio_data = {
            "code": muni_code_str,
            "name": muni_name,
            "lat": muni_lat,
            "lon": muni_lon,
            "total": len(puntos_out),
            "ok": statuses.count("ok"),
            "warning": statuses.count("warning"),
            "danger": statuses.count("danger"),
            "puntos": puntos_out,
        }
        municipios_out.append(municipio_data)

    return {
        "code": prov_code,
        "name": prov_name,
        "bounds": compute_bounds(all_lats, all_lons),
        "dates": date_pool,
        "dict": dict_entries,
        "municipios": municipios_out,
    }


# ── History building ────────────────────────────────────────────────


def build_provincia_history(
    prov_assessed: pd.DataFrame,
    param_to_idx: dict[str, int],
) -> dict:
    """Build historical time series for all puntos in a provincia.

    Returns {
        "s": {
            "municipio_code|punto_nombre": {
                "param_idx": [[date_str, valor], ...],
                ...
            }
        }
    }
    """
    series: dict[str, dict[str, list]] = {}

    for (muni_code, punto_nombre, param_boe), group in prov_assessed.groupby(
        ["municipio_code", "punto_muestreo", "parametro_boe"], sort=True
    ):
        if param_boe not in param_to_idx:
            continue
        if len(group) < 2:
            continue  # No chart needed for single measurements

        pidx = str(param_to_idx[param_boe])
        key = f"{muni_code}|{punto_nombre}"

        # Collect (date, value) sorted by date
        entries = []
        for _, row in group.sort_values("fecha").iterrows():
            fecha = row["fecha"]
            if pd.isna(fecha):
                continue
            entries.append([
                fecha.strftime("%Y-%m-%d"),
                _safe_num(row.get("valor")),
            ])

        if len(entries) >= 2:
            if key not in series:
                series[key] = {}
            series[key][pidx] = entries

    return {"s": series}


# ── National overview ────────────────────────────────────────────────


def build_national_json(prov_dir: Path) -> dict:
    """Build lightweight national points file from already-built provincia JSONs.

    Each point is a compact array: [lat, lon, statusCode, provCode, muniCode, nombre]
    statusCode: 0=ok, 1=warning, 2=danger
    """
    STATUS_CODE = {"ok": 0, "warning": 1, "danger": 2}
    points = []
    for f in sorted(prov_dir.glob("*.json")):
        if "_hist" in f.name:
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        prov_code = f.stem  # Already padded: "01", "02", etc.
        for m in d["municipios"]:
            for p in m["puntos"]:
                if p.get("lat") is None:
                    continue
                sc = STATUS_CODE.get(p["status"], 0)
                points.append([p["lat"], p["lon"], sc, prov_code, m["code"], p["nombre"]])
    return {"p": points}


# ── JSON writing ────────────────────────────────────────────────────


def write_json(data: dict, path: Path) -> None:
    """Write JSON with compact formatting."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


# ── Main pipeline ───────────────────────────────────────────────────


def main() -> None:
    print("=" * 60)
    print("BUILDING DASHBOARD DATA")
    print("=" * 60)

    # Step 1: Load and process
    print("\n[1/9] Loading all_data.csv ...")
    df = pd.read_csv(ALL_DATA_FILE, low_memory=False)
    print(f"       {len(df):,} rows loaded")

    print("[2/9] Cleaning types ...")
    df = clean_types(df)

    print("[3/9] Merging with BOE limits ...")
    df = merge_with_limits(df)

    print("[4/9] Computing compliance ...")
    df = add_compliance(df)

    print("[5/9] Tagging latest measurements ...")
    df = tag_latest(df)

    # Split: latest for main view, assessed for history
    latest = df[df["es_ultimo_analisis"] == True].copy()  # noqa: E712
    assessed = df[df["aptitud"].notna()].copy()
    print(f"       {len(latest):,} rows in latest analyses")
    print(f"       {len(assessed):,} rows with compliance assessment")

    # Step 2: Load coordinates and names
    print("[6/9] Loading coordinates and names ...")
    punto_coords = load_punto_coords()
    muni_centroids = load_muni_centroids()
    prov_names, muni_names = load_location_names()

    # Step 3: Build JSONs per provincia
    print("[7/9] Building main JSON files ...")
    output_dir = DASHBOARD_DATA_DIR
    prov_dir = output_dir / "provincia"
    prov_dir.mkdir(parents=True, exist_ok=True)

    provincia_summaries = []
    total_ok = total_warning = total_danger = total_puntos = 0

    # Cache param_to_idx per provincia for history step
    prov_param_idx: dict[str, dict[str, int]] = {}

    for prov_code, prov_group in latest.groupby("provincia_code", sort=True):
        prov_code_str = str(int(prov_code)) if pd.notna(prov_code) else str(prov_code)
        prov_name = prov_names.get(prov_code_str, f"Provincia {prov_code_str}")
        prov_code_padded = f"{int(prov_code_str):02d}"

        # Build dictionary and date pool
        dict_entries, param_to_idx = build_param_dict(prov_group)
        date_pool, date_to_idx = build_date_pool(prov_group)
        prov_param_idx[prov_code_str] = param_to_idx

        prov_json = build_provincia_json(
            prov_code_str, prov_name, prov_group,
            param_to_idx, date_pool, date_to_idx, dict_entries,
            punto_coords, muni_centroids, muni_names,
        )

        n_ok = sum(m["ok"] for m in prov_json["municipios"])
        n_warning = sum(m["warning"] for m in prov_json["municipios"])
        n_danger = sum(m["danger"] for m in prov_json["municipios"])
        n_total = sum(m["total"] for m in prov_json["municipios"])

        total_ok += n_ok
        total_warning += n_warning
        total_danger += n_danger
        total_puntos += n_total

        write_json(prov_json, prov_dir / f"{prov_code_padded}.json")

        provincia_summaries.append({
            "code": prov_code_padded,
            "name": prov_name,
            "total": n_total,
            "ok": n_ok,
            "warning": n_warning,
            "danger": n_danger,
        })

        print(f"       {prov_name}: {n_total} puntos "
              f"({n_ok} ok, {n_warning} warning, {n_danger} danger) "
              f"[dict: {len(dict_entries)} params]")

    # Step 4: Build history files
    print("[8/9] Building history files ...")
    for prov_code, prov_group in assessed.groupby("provincia_code", sort=True):
        prov_code_str = str(int(prov_code)) if pd.notna(prov_code) else str(prov_code)
        prov_code_padded = f"{int(prov_code_str):02d}"

        param_to_idx = prov_param_idx.get(prov_code_str, {})
        if not param_to_idx:
            continue

        hist = build_provincia_history(prov_group, param_to_idx)
        write_json(hist, prov_dir / f"{prov_code_padded}_hist.json")

        n_puntos = len(hist["s"])
        n_series = sum(len(v) for v in hist["s"].values())
        print(f"       {prov_names.get(prov_code_str, prov_code_str)}: "
              f"{n_puntos} puntos, {n_series} series")

    # Step 5: Write index.json
    provincia_summaries.sort(key=lambda p: p["name"])
    index_data = {
        "generated_at": date.today().isoformat(),
        "summary": {
            "total": total_puntos,
            "ok": total_ok,
            "warning": total_warning,
            "danger": total_danger,
        },
        "provincias": provincia_summaries,
    }
    write_json(index_data, output_dir / "index.json")

    # Step 6: Build national.json (lightweight file with all points)
    print("[9/9] Building national.json ...")
    national = build_national_json(prov_dir)
    national_path = output_dir / "national.json"
    write_json(national, national_path)
    print(f"       {len(national['p']):,} points, "
          f"{national_path.stat().st_size / 1024:.0f} KB")

    # Summary
    main_size = sum(f.stat().st_size for f in prov_dir.glob("*.json") if "_hist" not in f.name) / 1024 / 1024
    hist_size = sum(f.stat().st_size for f in prov_dir.glob("*_hist.json")) / 1024 / 1024
    print(f"\n{'=' * 60}")
    print(f"DONE — {len(provincia_summaries)} provincias")
    print(f"Total: {total_puntos} puntos ({total_ok} ok, {total_warning} warning, {total_danger} danger)")
    print(f"Main JSON: {main_size:.1f} MB | History JSON: {hist_size:.1f} MB")
    print(f"Output: {output_dir}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
