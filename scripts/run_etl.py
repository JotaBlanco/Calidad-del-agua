"""
run_etl.py — Thin orchestrator for the water-quality ETL pipeline.

Usage:
    python -m scripts.run_etl          # run full pipeline
    python -m scripts.run_etl --diag   # diagnostics only (no file writes)
"""

from __future__ import annotations

import argparse

import pandas as pd

from scripts.etl import (
    ALL_DATA_FILE,
    build_expanded_limits,
    merge_with_limits,
    add_compliance,
    normalize_parametro,
    is_pesticide,
    COMPONENT_TO_SUM_MAP,
)


# ── helpers ───────────────────────────────────────────────────────────
def _pct(num: int, den: int) -> str:
    return f"{num / den * 100:.1f}%" if den else "N/A"


# ── diagnostics ───────────────────────────────────────────────────────
def run_diagnostics(df: pd.DataFrame, merged: pd.DataFrame) -> None:
    """Print step-by-step coverage stats for the ETL pipeline."""

    total_rows = len(df)
    unique_params = df["parametro"].dropna().unique()
    n_unique = len(unique_params)

    # Step 1: direct BOE matches + rename matches
    boe_names = set(
        build_expanded_limits(data_params=unique_params)
        .query("source == 'BOE'")["parametro"]
    )
    renamed = {normalize_parametro(p) for p in unique_params}
    direct = sum(1 for p in unique_params if p in boe_names)
    after_rename = sum(1 for p in renamed if p in boe_names)

    print("=" * 60)
    print("ETL COVERAGE DIAGNOSTICS")
    print("=" * 60)
    print(f"Total rows         : {total_rows:,}")
    print(f"Unique parameters  : {n_unique:,}")
    print()

    print("Step 1 — Normalization")
    print(f"  Direct BOE match : {direct} params")
    print(f"  After rename     : {after_rename} params (+{after_rename - direct})")
    print()

    # Step 2: pesticides
    pest_params = [p for p in unique_params if is_pesticide(p)]
    print(f"Step 2 — Pesticide expansion")
    print(f"  Individual pesticides in data: {len(pest_params)}")
    print()

    # Step 3: summation components
    comp_params = [p for p in unique_params if p in COMPONENT_TO_SUM_MAP]
    print(f"Step 3 — Summation components")
    print(f"  Components in data: {len(comp_params)}")
    print()

    # Overall merge coverage
    linked = merged["parametro_boe"].notna() & merged["source_limite"].notna()
    n_linked = linked.sum()
    linked_params = (
        merged.loc[linked, "parametro_boe"].nunique()
    )
    unlinked_params = (
        merged.loc[~linked, "parametro"].dropna().nunique()
    )
    print("Overall merge result")
    print(f"  Rows linked      : {n_linked:,} / {total_rows:,} ({_pct(n_linked, total_rows)})")
    print(f"  Params linked    : {linked_params}")
    print(f"  Params unlinked  : {unlinked_params}")
    print()

    # Step 4: compliance
    if "aptitud" in merged.columns:
        counts = merged["aptitud"].value_counts(dropna=False)
        assessed = merged["aptitud"].notna().sum()
        print("Step 4 — Compliance")
        print(f"  Rows assessed    : {assessed:,} / {total_rows:,} ({_pct(assessed, total_rows)})")
        for label in ["apta", "incumple_vp", "no_apta"]:
            n = counts.get(label, 0)
            print(f"    {label:14s} : {n:,} ({_pct(n, assessed)})")

        if "aptitud_suma" in merged.columns:
            sum_assessed = merged["aptitud_suma"].notna().sum()
            sum_counts = merged["aptitud_suma"].value_counts(dropna=False)
            print(f"  Summation rows   : {sum_assessed:,}")
            for label in ["apta", "no_apta"]:
                n = sum_counts.get(label, 0)
                print(f"    {label:14s} : {n:,}")
        print()

    print("=" * 60)


# ── main pipeline ─────────────────────────────────────────────────────
def run(diag_only: bool = False) -> pd.DataFrame:
    """Execute the full ETL pipeline and return the merged DataFrame."""

    print("Loading data …")
    df = pd.read_csv(ALL_DATA_FILE, low_memory=False)

    print("Building expanded limits …")
    limits = build_expanded_limits(
        data_params=df["parametro"].dropna().unique()
    )

    print("Merging …")
    merged = merge_with_limits(df, limits=limits)

    print("Checking compliance …")
    merged = add_compliance(merged)

    run_diagnostics(df, merged)

    if not diag_only:
        out = ALL_DATA_FILE.parent / "all_data_enriched.csv"
        merged.to_csv(out, index=False)
        print(f"\nSaved → {out}")

    return merged


# ── CLI ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Water-quality ETL pipeline")
    parser.add_argument("--diag", action="store_true", help="Diagnostics only")
    args = parser.parse_args()
    run(diag_only=args.diag)
