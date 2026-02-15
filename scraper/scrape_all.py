"""
SINAC Batch Scraper — scrape all municipalities in Spain.

Reads the locations catalog, scrapes each municipality, and saves
one CSV per municipality plus a progress tracker for resumability.

Each province gets its own progress file, so multiple processes can
run in parallel (one per province) without conflicts.

Usage:
    python scrape_all.py                    # scrape everything (sequential)
    python scrape_all.py --ccaa 12          # scrape only Galicia
    python scrape_all.py --provincia 36     # scrape only Pontevedra
    python scrape_all.py --skip-pdfs        # don't save individual PDFs
    python scrape_all.py --parallel         # launch all provinces in parallel
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import pdfplumber
import requests
from bs4 import BeautifulSoup

# Import shared functions from scrape.py
from scrape import (
    BASE_URL,
    ANALYSIS_TYPE_MAP,
    create_session,
    get_provinces,
    get_municipalities,
    get_distribution_networks,
    get_boletins,
    download_pdf,
    parse_pdf,
    sanitize_filename,
)

DATA_DIR = Path(__file__).parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
CSV_DIR = RAW_DIR / "csvs"
PDF_DIR = RAW_DIR / "pdfs"
PROGRESS_DIR = DATA_DIR / "progress"
CATALOG_FILE = DATA_DIR / "locations_catalog.csv"


def progress_file_for(provincia_code):
    """Each province gets its own progress file to avoid conflicts."""
    return PROGRESS_DIR / f"progress_{provincia_code}.json"


def load_progress(provincia_code):
    """Load scraping progress for a province."""
    pf = progress_file_for(provincia_code)
    if pf.exists():
        with open(pf) as f:
            return json.load(f)
    return {"completed": [], "failed": [], "stats": {"total_records": 0, "total_boletins": 0}}


def save_progress(progress, provincia_code):
    """Save scraping progress for a province."""
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    with open(progress_file_for(provincia_code), "w") as f:
        json.dump(progress, f, indent=2)


def scrape_municipality_safe(ccaa_code, provincia_code, municipio_code,
                              session=None, save_pdfs=True, max_retries=3):
    """Scrape a municipality with retry logic. Returns (records, municipio_name, error_msg).

    If session is provided, reuses it (skips session setup). Falls back to a
    fresh session on retry.
    """
    for attempt in range(max_retries):
        try:
            if session is None or attempt > 0:
                session = create_session()
                get_provinces(session, ccaa_code)
                get_municipalities(session, provincia_code)

            networks, municipio_name = get_distribution_networks(
                session, ccaa_code, provincia_code, municipio_code
            )

            if not municipio_name:
                return [], None, "No municipality name found"

            # Download boletins per-network (server is stateful: must be on
            # the network detail page when downloading its boletins)
            all_records = []

            for network_id, network_display_name in networks.items():
                boletins, network_name = get_boletins(
                    session, provincia_code, municipio_code,
                    municipio_name, network_id,
                )

                for boletin in boletins:
                    bid = boletin["boletin_id"]
                    date_str = boletin["date"]
                    analysis_type = boletin["analysis_type"]

                    pdf_bytes = download_pdf(
                        session, provincia_code, municipio_code,
                        municipio_name, network_id, network_name, bid,
                    )

                    if pdf_bytes is None:
                        continue

                    if save_pdfs:
                        date_clean = date_str.split(" ")[0]
                        try:
                            parts = date_clean.split("/")
                            date_prefix = f"{parts[2]}{parts[1]}{parts[0]}"
                        except (IndexError, ValueError):
                            date_prefix = date_clean.replace("/", "")

                        safe_municipio = sanitize_filename(municipio_name)
                        safe_network = sanitize_filename(network_name)
                        pdf_filename = f"{date_prefix}_{safe_municipio}_{safe_network}_{analysis_type}.pdf"
                        pdf_path = PDF_DIR / pdf_filename
                        pdf_path.parent.mkdir(parents=True, exist_ok=True)
                        pdf_path.write_bytes(pdf_bytes)

                    metadata, parameters = parse_pdf(pdf_bytes)

                    for param in parameters:
                        all_records.append({
                            "ccaa_code": ccaa_code,
                            "provincia_code": provincia_code,
                            "municipio_code": municipio_code,
                            "municipio": municipio_name,
                            "red": network_name,
                            "punto_muestreo": metadata.get("punto_muestreo", ""),
                            "zona_abastecimiento": metadata.get("zona_abastecimiento", ""),
                            "fecha_toma": metadata.get("fecha_toma", ""),
                            "tipo_boletin": metadata.get("tipo_boletin", ""),
                            "tipo_analisis": metadata.get("tipo_analisis", ""),
                            "laboratorio": metadata.get("laboratorio", ""),
                            "calificacion": metadata.get("calificacion", ""),
                            "boletin_id": bid,
                            "parametro": param["parametro"],
                            "valor": param["valor"],
                            "unidad": param["unidad"],
                        })

                    time.sleep(0.15)

            return all_records, municipio_name, None

        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 5
                print(f"    Retry {attempt + 1}/{max_retries} after error: {e}")
                session = None  # Force fresh session on retry
                time.sleep(wait)
            else:
                return [], None, f"{type(e).__name__}: {e}"

    return [], None, "Max retries exceeded"


def scrape_province(catalog, prov_code, skip_pdfs=False, reset=False):
    """Scrape all municipalities in a single province."""
    prov_catalog = catalog[catalog["provincia_code"] == int(prov_code)]
    prov_name = prov_catalog.iloc[0]["provincia_name"] if len(prov_catalog) > 0 else prov_code
    ccaa_code = str(prov_catalog.iloc[0]["ccaa_code"])

    if reset:
        progress = {"completed": [], "failed": [], "stats": {"total_records": 0, "total_boletins": 0}}
    else:
        progress = load_progress(prov_code)

    completed_set = set(progress["completed"])
    total = len(prov_catalog)
    already_done = sum(1 for _, row in prov_catalog.iterrows()
                       if str(row["municipio_code"]) in completed_set)

    print(f"[Province {prov_code} - {prov_name}] {already_done}/{total} already completed")

    CSV_DIR.mkdir(parents=True, exist_ok=True)

    # Reuse one session for the whole province (saves 3 HTTP requests per municipality)
    session = create_session()
    get_provinces(session, ccaa_code)
    get_municipalities(session, prov_code)

    for _, row in prov_catalog.iterrows():
        muni_code = str(row["municipio_code"])

        if muni_code in completed_set:
            continue

        already_done += 1
        print(f"[{prov_name} {already_done}/{total}] {row['municipio_name']}")

        records, muni_name, error = scrape_municipality_safe(
            ccaa_code, prov_code, muni_code,
            session=session,
            save_pdfs=not skip_pdfs,
        )

        if error:
            print(f"  FAILED: {error}")
            progress["failed"].append({
                "municipio_code": muni_code,
                "municipio_name": row["municipio_name"],
                "error": error,
            })
            # Refresh session after failure
            session = create_session()
            get_provinces(session, ccaa_code)
            get_municipalities(session, prov_code)
        else:
            if records:
                safe_name = sanitize_filename(muni_name or row["municipio_name"])
                csv_path = CSV_DIR / f"{prov_code}_{muni_code}_{safe_name}.csv"
                df = pd.DataFrame(records)
                df.to_csv(csv_path, index=False)
                print(f"  OK: {len(records)} records")
                progress["stats"]["total_records"] += len(records)
            else:
                print(f"  OK: no data")

        progress["completed"].append(muni_code)
        completed_set.add(muni_code)
        save_progress(progress, prov_code)
        time.sleep(0.1)

    print(f"\n[{prov_name}] DONE! {len(progress['completed'])}/{total} completed, "
          f"{len(progress['failed'])} failed, {progress['stats']['total_records']} records")
    return progress


def run_province_subprocess(prov_code, prov_name, script, venv_python, log_dir,
                            skip_pdfs=False, reset=False):
    """Run a single province scraper as a subprocess. Used as a pool worker."""
    log_file = log_dir / f"scrape_{prov_code}.log"

    cmd = [str(venv_python), "-u", str(script), "--provincia", str(prov_code)]
    if skip_pdfs:
        cmd.append("--skip-pdfs")
    if reset:
        cmd.append("--reset")

    print(f"  START  province {prov_code} ({prov_name})")

    with open(log_file, "w") as lf:
        result = subprocess.run(
            cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=str(script.parent),
        )

    status = "OK" if result.returncode == 0 else f"EXIT {result.returncode}"
    print(f"  DONE   province {prov_code} ({prov_name}): {status}")
    return prov_code, prov_name, result.returncode


def launch_parallel(catalog, provinces, skip_pdfs=False, reset=False,
                    max_workers=4):
    """Launch province scrapers using a thread pool — workers pick the next
    province from the queue as soon as they finish, so no idle waiting."""
    script = Path(__file__).resolve()
    venv_python = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python3"

    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    prov_names = {
        pc: catalog[catalog["provincia_code"] == int(pc)].iloc[0]["provincia_name"]
        for pc in provinces
    }

    print(f"Queued {len(provinces)} provinces with {max_workers} workers.")
    print(f"Monitor progress with:")
    print(f"  python scrape_all.py --status")
    print(f"  tail -f data/logs/scrape_*.log")
    print(f"\nTo keep your Mac awake, run in another terminal:")
    print(f"  caffeinate -i -w {os.getpid()}\n")

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    run_province_subprocess,
                    pc, prov_names[pc], script, venv_python, log_dir,
                    skip_pdfs, reset,
                ): pc
                for pc in provinces
            }

            for future in as_completed(futures):
                prov_code, prov_name, returncode = future.result()
                if returncode != 0:
                    print(f"  WARNING: province {prov_code} ({prov_name}) "
                          f"exited with code {returncode}")
    except KeyboardInterrupt:
        print("\nInterrupted! Waiting for running workers to finish...")
        pool.shutdown(wait=True, cancel_futures=True)
        print("Stopped. Progress is saved — you can resume with the same command.")


def show_status(catalog):
    """Show progress across all provinces."""
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)

    total_completed = 0
    total_failed = 0
    total_records = 0
    total_munis = len(catalog)

    provinces = sorted(catalog["provincia_code"].unique())

    print(f"{'Province':<30} {'Done':>6} {'Total':>6} {'Failed':>6} {'Records':>10}")
    print("-" * 70)

    for prov_code in provinces:
        prov_catalog = catalog[catalog["provincia_code"] == prov_code]
        prov_name = prov_catalog.iloc[0]["provincia_name"]
        prov_total = len(prov_catalog)

        progress = load_progress(str(prov_code))
        done = len(progress["completed"])
        failed = len(progress["failed"])
        records = progress["stats"]["total_records"]

        total_completed += done
        total_failed += failed
        total_records += records

        if done > 0 or failed > 0:
            pct = done / prov_total * 100
            print(f"{prov_name:<30} {done:>6} {prov_total:>6} {failed:>6} {records:>10}  ({pct:.0f}%)")

    print("-" * 70)
    pct = total_completed / total_munis * 100 if total_munis else 0
    print(f"{'TOTAL':<30} {total_completed:>6} {total_munis:>6} {total_failed:>6} {total_records:>10}  ({pct:.0f}%)")


def main():
    parser = argparse.ArgumentParser(description="Batch scrape SINAC water quality data")
    parser.add_argument("--ccaa", type=int, help="Filter by CCAA code")
    parser.add_argument("--provincia", type=int, help="Filter by province code")
    parser.add_argument("--skip-pdfs", action="store_true", help="Don't save individual PDFs")
    parser.add_argument("--reset", action="store_true", help="Reset progress and start over")
    parser.add_argument("--parallel", action="store_true",
                        help="Launch one process per province in parallel")
    parser.add_argument("--status", action="store_true", help="Show progress across all provinces")
    parser.add_argument("--max-parallel", type=int, default=4,
                        help="Max parallel processes (default: 4)")
    args = parser.parse_args()

    if not CATALOG_FILE.exists():
        print("ERROR: Run enumerate_locations.py first to generate the catalog.")
        sys.exit(1)

    catalog = pd.read_csv(CATALOG_FILE)

    # Status mode
    if args.status:
        show_status(catalog)
        return

    # Apply filters
    if args.ccaa:
        catalog = catalog[catalog["ccaa_code"] == args.ccaa]
    if args.provincia and not args.parallel:
        catalog = catalog[catalog["provincia_code"] == args.provincia]

    print(f"Loaded catalog: {len(catalog)} municipalities")

    if args.parallel:
        # Get list of provinces to scrape
        all_provinces = sorted(catalog["provincia_code"].unique())
        if args.provincia:
            provinces = [args.provincia]
        else:
            # Galician provinces first, then the rest alphabetically
            galicia = [15, 27, 32, 36]
            rest = [p for p in all_provinces if p not in galicia]
            provinces = galicia + rest

        print(f"Queuing {len(provinces)} provinces with up to {args.max_parallel} workers...\n")
        launch_parallel(catalog, provinces, args.skip_pdfs, args.reset,
                        max_workers=args.max_parallel)

    else:
        # Single province mode
        if args.provincia:
            scrape_province(catalog, str(args.provincia), args.skip_pdfs, args.reset)
        else:
            # Sequential: iterate all provinces
            provinces = sorted(catalog["provincia_code"].unique())
            for prov_code in provinces:
                scrape_province(catalog, str(prov_code), args.skip_pdfs, args.reset)


if __name__ == "__main__":
    main()
