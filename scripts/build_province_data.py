"""
build_province_data.py — Rebuild the dashboard JSON for individual provinces.

``build_dashboard_data.py`` rebuilds the whole country: it loads the 2.3 GB
``all_data.csv`` into one DataFrame, which needs more RAM than a GitHub-hosted
runner has.  That is why the nightly workflow used to scrape data it could never
publish — the dashboard under ``docs/dashboard/data/`` stayed frozen at whatever
was last built by hand.

Nothing about the output actually needs the whole country in memory at once:

  * ``provincia/{code}.json`` and ``provincia/{code}_hist.json`` are built from
    one province's rows and nothing else — see the ``groupby("provincia_code")``
    loops in ``build_dashboard_data.main()``.
  * ``national.json`` is assembled from the province files already on disk.
  * ``index.json`` is per-province summaries, which the province files carry.

So this script does the same s0–s5 ETL on ``data/raw/csvs/{prov}_*.csv`` — the
scraper's own output, no ``all_data.csv`` in between — and writes the same two
files, then refreshes ``index.json`` and ``national.json`` from disk.  Peak
memory is set by the largest province (Las Palmas, ~146 MB of raw CSV), which
fits a standard runner with room to spare.

The output is byte-identical to the whole-country build.  The one ETL value that
depends on the frame it is given, ``antiguedad_dias`` (``tag_latest`` measures
against the newest date *in the data*), never reaches the dashboard: it only
gates ``mediciones_vigentes``, and ``MAX_ANTIGUEDAD_DIAS`` is ``None``.

``docs/dashboard/data/`` belongs to the nightly workflow, which commits it, so
writing there needs an explicit ``--publish`` — see ``require_publish``.

Usage:
    python -m scripts.build_province_data 15 27 --publish   # two provinces
    python -m scripts.build_province_data --all --publish   # every province
    python -m scripts.build_province_data --index-only --publish
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from scripts.build_dashboard_data import (
    DASHBOARD_DATA_DIR,
    build_date_pool,
    build_national_json,
    build_param_dict,
    build_provincia_history,
    build_provincia_json,
    load_location_names,
    load_muni_centroids,
    load_punto_coords,
    require_publish,
    write_json,
)
from scripts.etl import (
    DATA_DIR,
    add_compliance,
    clean_types,
    mediciones_vigentes,
    merge_with_limits,
    tag_latest,
)

CSV_DIR = DATA_DIR / "raw" / "csvs"
PROV_DIR = DASHBOARD_DATA_DIR / "provincia"

# Columns pandas infers as integers when it reads the whole all_data.csv.  The
# per-province CSVs are read as strings (the scraper writes them that way), so
# these are converted back — otherwise municipio_code would group and sort
# lexicographically and the JSON would come out in a different order.
CODE_COLS = ("ccaa_code", "provincia_code", "municipio_code", "boletin_id")


# ── loading ───────────────────────────────────────────────────────────


def load_province_frame(code: int) -> pd.DataFrame | None:
    """Concatenate every raw CSV belonging to one province."""
    files = sorted(CSV_DIR.glob(f"{code}_*.csv"))
    if not files:
        return None

    frames, unreadable = [], []
    for path in files:
        try:
            frames.append(pd.read_csv(path, dtype=str))
        except Exception as exc:  # a truncated CSV must not sink the province
            unreadable.append((path.name, str(exc)))

    if unreadable:
        print(f"       aviso: {len(unreadable)} CSV ilegibles")
        for name, err in unreadable[:5]:
            print(f"         - {name}: {err}")

    if not frames:
        return None

    df = pd.concat(frames, ignore_index=True)

    for col in CODE_COLS:
        if col in df.columns:
            numeric = pd.to_numeric(df[col], errors="coerce")
            if numeric.notna().all():
                df[col] = numeric.astype("int64")

    # Belt and braces: a stray row from another province would otherwise land
    # in this province's file, where the whole-country build would not put it.
    if "provincia_code" in df.columns:
        df = df[pd.to_numeric(df["provincia_code"], errors="coerce") == code]

    return df.reset_index(drop=True)


# ── per-province build ────────────────────────────────────────────────


def build_province(code: int, coords: dict, centroids: dict,
                   prov_names: dict, muni_names: dict) -> dict | None:
    """Rebuild both JSON files for one province. Returns its summary row."""
    padded = f"{code:02d}"
    name = prov_names.get(str(code), f"Provincia {code}")
    print(f"[{padded}] {name}")

    df = load_province_frame(code)
    if df is None or df.empty:
        print("       sin CSVs, se deja el JSON existente intacto")
        return None
    print(f"       {len(df):,} filas en {len(list(CSV_DIR.glob(f'{code}_*.csv')))} CSVs")

    df = clean_types(df)
    df = merge_with_limits(df)
    df = add_compliance(df)
    df = tag_latest(df)

    latest = mediciones_vigentes(df).copy()
    assessed = df[df["aptitud"].notna()].copy()

    dict_entries, param_to_idx = build_param_dict(latest)
    date_pool, date_to_idx = build_date_pool(latest)

    prov_json = build_provincia_json(
        str(code), name, latest,
        param_to_idx, date_pool, date_to_idx, dict_entries,
        coords, centroids, muni_names,
    )
    write_json(prov_json, PROV_DIR / f"{padded}.json")

    if param_to_idx:
        hist = build_provincia_history(assessed, param_to_idx)
        write_json(hist, PROV_DIR / f"{padded}_hist.json")
        n_series = sum(len(v) for v in hist["s"].values())
    else:
        n_series = 0

    counts = {k: sum(m[k] for m in prov_json["municipios"])
              for k in ("total", "ok", "warning", "danger")}
    print(f"       {counts['total']} puntos "
          f"({counts['ok']} ok, {counts['warning']} warning, {counts['danger']} danger), "
          f"{n_series} series históricas")

    return {"code": padded, "name": name, **counts}


# ── index.json and national.json ──────────────────────────────────────


def rebuild_index_and_national() -> None:
    """Reassemble the two country-wide files from the province files on disk."""
    summaries: list[dict] = []
    totals = {"total": 0, "ok": 0, "warning": 0, "danger": 0}

    for path in sorted(PROV_DIR.glob("*.json")):
        if "_hist" in path.name:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        counts = {k: sum(m[k] for m in data["municipios"])
                  for k in ("total", "ok", "warning", "danger")}
        summaries.append({"code": path.stem, "name": data["name"], **counts})
        for key in totals:
            totals[key] += counts[key]

    summaries.sort(key=lambda p: p["name"])
    write_json(
        {
            "generated_at": date.today().isoformat(),
            "summary": totals,
            "provincias": summaries,
        },
        DASHBOARD_DATA_DIR / "index.json",
    )

    national = build_national_json(PROV_DIR)
    write_json(national, DASHBOARD_DATA_DIR / "national.json")
    print(f"index.json: {len(summaries)} provincias, {totals['total']:,} puntos")
    print(f"national.json: {len(national['p']):,} puntos")


# ── CLI ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("provincias", nargs="*", type=int, metavar="CODE",
                        help="Province codes to rebuild")
    parser.add_argument("--all", action="store_true",
                        help="Rebuild every province that has raw CSVs")
    parser.add_argument("--index-only", action="store_true",
                        help="Only refresh index.json and national.json")
    parser.add_argument("--publish", action="store_true",
                        help="Write into docs/dashboard/data/ (CI passes this)")
    args = parser.parse_args(argv)

    require_publish(args.publish)
    PROV_DIR.mkdir(parents=True, exist_ok=True)

    codes = args.provincias
    if args.all:
        # pathlib's glob matches dotfiles.  Untarring the macOS-built seed on
        # Linux leaves an AppleDouble `._` twin beside every CSV, and
        # int("._21".split("_")[0]) is a ValueError.
        codes = sorted({int(p.name.split("_")[0]) for p in CSV_DIR.glob("*.csv")
                        if not p.name.startswith(".")})

    if not codes and not args.index_only:
        parser.error("pass province codes, --all, or --index-only")

    if codes:
        coords = load_punto_coords()
        centroids = load_muni_centroids()
        prov_names, muni_names = load_location_names()

        built = 0
        for code in codes:
            if build_province(code, coords, centroids, prov_names, muni_names):
                built += 1
        print(f"\n{built}/{len(codes)} provincias reconstruidas")

    rebuild_index_and_national()
    return 0


if __name__ == "__main__":
    sys.exit(main())
