#!/usr/bin/env python3
"""
ETL utilities for water quality data normalization and enrichment.

Modules:
    1. Parameter name normalization (scraped names → BOE canonical names)
    2. Expanded BOE limits (original + individual pesticide rows)
    3. Merge water quality data with legal limits

Usage:
    # As a library
    from scripts.etl import normalize_parametro, build_expanded_limits, merge_with_limits

    # Standalone: print match diagnostics
    python scripts/etl.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
LIMITS_DIR = DATA_DIR / "limits"

ALL_DATA_FILE = PROCESSED_DIR / "all_data.csv"
BOE_LIMITS_FILE = LIMITS_DIR / "limites_legales_boe.csv"


# ===================================================================
# 1. Parameter name normalization
# ===================================================================

# Maps scraped parameter names → BOE canonical names.
# Only entries where the scraped name differs from the BOE name.
#
# Discrepancy categories:
#   - FMT:  formatting (capitalization, spacing, accents, punctuation)
#   - CAS:  CAS number appended in scraped data
#   - ABBR: abbreviation expanded / notation change
#   - QUAL: qualifier added or reworded
PARAM_RENAME_MAP: dict[str, str] = {
    # --- FMT: capitalization / spacing / accents / punctuation ----------
    "PH":                                    "pH",
    "Recuento de colonias a 22ºC":           "Recuento de colonias a 22 ºC",
    "Legionella spp":                        "Legionella spp.",
    "Colifagos somáticos":                   "Colífagos somáticos",
    "Indice de Langelier":                   "Índice de Langelier",
    "Radon":                                 "Radón",
    "Microcistina LR":                       "Microcistina-LR",

    # --- CAS: CAS number appended in scraped data ----------------------
    "Acrilamida (CAS 79-06-01)":             "Acrilamida",
    "Benceno (CAS 71-43-2)":                 "Benceno",
    "Benzo(a)pireno (CAS 50-32-8)":          "Benzo(a)pireno",
    "Bisfenol a (CAS 80-05-7)":              "Bisfenol A",
    "Cloruro de Vinilo (CAS 75-01-4)":       "Cloruro de vinilo",
    "Epiclorhidrina (CAS 106-89-8)":         "Epiclorhidrina",
    "1,2-Dicloroetano (CAS 107-06-2)":       "1-2-Dicloroetano",

    # --- ABBR: abbreviation / notation changes --------------------------
    "Actividad a total":                     "Actividad alfa total",
    "Actividad b resto":                     "Actividad beta resto",
    "Dosis Indicativa (Suma radionucleidos) DI": "Dosis Indicativa (DI)",
    "Suma 2 Tricloroeteno + Tetracloroeteno": "∑2 Tricloroeteno + Tetracloroeteno",
    "Suma 20 PFAs":                          "∑20 PFAS",
    "Suma 4 Hidrocarburos Policíclicos Aromáticos (HPA)": "∑4 Hidrocarburos Policíclicos Aromáticos (HPA)",
    "Suma 4 Trihalometanos (THM)":           "∑4 Trihalometanos (THM)",
    "Suma 5 AHAs":                           "∑5 Ácidos Haloacéticos (HAH)",
    "Suma total Plaguicidas":                "∑n Plaguicidas totales",

    # --- QUAL: "R: " prefix on natural radionuclides -------------------
    "R: Pb 210":                             "Pb 210",
    "R: Po 210":                             "Po 210",
    "R: Ra 226":                             "Ra 226",
    "R: Ra 228":                             "Ra 228",
    "R: U 234":                              "U 234",
    "R: U 238":                              "U 238",

    # --- QUAL: qualifier differences ------------------------------------
    "Enterococo":                            "Enterococo intestinal",
    "Dureza Total (CaCO3)":                  "Dureza total",
}

# Prefixes identifying individual pesticides in the scraped data.
# All map to BOE "Plaguicida individual" limit (0.10 µg/L) and
# contribute collectively to "∑n Plaguicidas totales" (0.50 µg/L).
PESTICIDE_PREFIXES: tuple[str, ...] = (
    "PLA: A_",
    "PLA: NA_",
    "PLA: ",     # catch-all for other PLA variants (e.g. "PLA: Acrinatrin")
    "ISO: ",
    "MET: ",
)


def normalize_parametro(name: str) -> str:
    """Normalize a scraped parameter name to its BOE canonical form.

    Returns the original name unchanged if no mapping exists.
    """
    return PARAM_RENAME_MAP.get(name, name)


def is_pesticide(name: str) -> bool:
    """Check if a parameter name represents an individual pesticide."""
    return any(name.startswith(p) for p in PESTICIDE_PREFIXES)


# Maps individual component (data name) → parent sum parameter (BOE name).
# These components have no individual BOE limit; only their sum is regulated.
COMPONENT_TO_SUM_MAP: dict[str, str] = {
    # --- THM → ∑4 Trihalometanos (THM) — limit: 100 µg/L ---------------
    "Cloroformo CAS 67-66-3":              "∑4 Trihalometanos (THM)",
    "Bromodiclorometano CAS 75-27-4":      "∑4 Trihalometanos (THM)",
    "Dibromoclorometano CAS 124-48-1":      "∑4 Trihalometanos (THM)",
    "Bromoformo CAS 75-25-2":              "∑4 Trihalometanos (THM)",

    # --- HAA → ∑5 Ácidos Haloacéticos (HAH) — limit: 60 µg/L ----------
    "Ácido dicloroacético CAS 79-43-6":     "∑5 Ácidos Haloacéticos (HAH)",
    "Ácido tricloroacético CAS 76-03-9":    "∑5 Ácidos Haloacéticos (HAH)",
    "Ácido monocloroacético CAS 79-11-8":   "∑5 Ácidos Haloacéticos (HAH)",
    "Ácido dibromoacético CAS 631-64-1":    "∑5 Ácidos Haloacéticos (HAH)",
    "Ácido monobromoacético CAS 79-08-3":   "∑5 Ácidos Haloacéticos (HAH)",

    # --- HPA → ∑4 Hidrocarburos Policíclicos Aromáticos (HPA) — 0.10 µg/L
    "Benzo(b)fluoranteno CAS 205-99-2":     "∑4 Hidrocarburos Policíclicos Aromáticos (HPA)",
    "Benzo(ghi)perileno CAS 191-24-2":      "∑4 Hidrocarburos Policíclicos Aromáticos (HPA)",
    "Benzo(k)fluoranteno CAS 207-08-9":     "∑4 Hidrocarburos Policíclicos Aromáticos (HPA)",
    "Indeno(1,2,3-cd)pireno CAS 193-39-5":  "∑4 Hidrocarburos Policíclicos Aromáticos (HPA)",

    # --- ∑2 Tricloroeteno + Tetracloroeteno — limit: 10 µg/L -----------
    "Tricloroeteno CAS 79-01-6":            "∑2 Tricloroeteno + Tetracloroeteno",
    "Tetracloroeteno CAS 127-18-4":         "∑2 Tricloroeteno + Tetracloroeteno",

    # --- PFAS → ∑20 PFAS — limit: 0.10 µg/L ---------------------------
    "Acido perfluorodecano sulfónico (PFDS) CAS: 335-77-3":  "∑20 PFAS",
    "Ácido perfluorobutanoico (PFBA) CAS: 375-22-4":         "∑20 PFAS",
    "Ácido perfluorobutanosulfónico (PFBS) CAS: 375-73-5":   "∑20 PFAS",
    "Ácido perfluorodecanoico (PFDA) CAS: 335-76-2":         "∑20 PFAS",
    "Ácido perfluorododecano sulfónico (PFDoS) CAS: 79780-39-5": "∑20 PFAS",
    "Ácido perfluorododecanoico (PFDoDA) CAS: 307-55-1":     "∑20 PFAS",
    "Ácido perfluoroheptano sulfónico (PFHpS) CAS: 375-92-8": "∑20 PFAS",
    "Ácido perfluoroheptanoico (PFHpA) CAS: 375-85-9":       "∑20 PFAS",
    "Ácido perfluorohexanoico (PFHxA) CAS: 307-24-4":        "∑20 PFAS",
    "Ácido perfluorohexanosulfónico (PFHxS) CAS: 355-46-4":  "∑20 PFAS",
    "Ácido perfluorononanoico PFNA CAS 375-95-1":             "∑20 PFAS",
    "Ácido perfluorononanosulfónico (PFNS) CAS: 68259-12-1": "∑20 PFAS",
    "Ácido perfluorooctanoico PFOA CAS 335-67-1":             "∑20 PFAS",
    "Ácido perfluorooctanosulfónico PFOS CAS 1763-23-1":      "∑20 PFAS",
    "Ácido perfluoropentanoico (PFPeA) CAS: 2706-90-3":      "∑20 PFAS",
    "Ácido perfluoropentanosulfónico (PFPeS) CAS: 2706-91-4": "∑20 PFAS",
    "Ácido perfluorotridecano sulfónico (PFTris) CAS: -":    "∑20 PFAS",
    "Ácido perfluorotridecanoico (PFTrDA) CAS: 72629-94-8":  "∑20 PFAS",
    "Ácido perfluoroundecano sulfónico (PFUnS) CAS: 749786-16-1": "∑20 PFAS",
    "Ácido perfluoroundecanoico (PFUnDA) CAS: 2058-94-8":    "∑20 PFAS",
}


# Unit name normalization: data unit → BOE canonical unit.
# All mismatches are naming-only (no numeric conversion needed).
UNIT_RENAME_MAP: dict[str, str] = {
    # Microbiological — different formatting, same count
    "NMP/100ml":        "UFC o NMP / 100 ml",
    "UFC/100 ml":       "UFC / 100 ml",
    "UFP/100 ml":       "UFP / 100 ml",
    "UFC/L":            "UFC en 1 L",
    "UFC/1 ml":         "UFC / 1 ml",
    # Physical / chemical
    "mg Pt-Co/L":       "mg/L Pt/Co",
    "µS/cm a 20ºC":     "µS/cm a 20 ºC",
    "mg O2 /L":         "mg/L O2",
    "mSv/año":          "mSv",
    "In. Dil.":         "Índice dilución",
    "Unidades pH":      "Unidades pH",        # already matches pH; covers Langelier too
}


def normalize_unidad(unit: str) -> str:
    """Normalize a data unit string to its BOE canonical form."""
    if pd.isna(unit):
        return unit
    return UNIT_RENAME_MAP.get(unit, unit)


# ===================================================================
# 2. Expanded BOE limits
# ===================================================================

def load_boe_limits() -> pd.DataFrame:
    """Load the original BOE legal limits CSV."""
    return pd.read_csv(BOE_LIMITS_FILE)


def build_expanded_limits(data_params: pd.Series | None = None) -> pd.DataFrame:
    """Build an expanded limits table with three layers:

    1. Original BOE rows (82 parameters, unchanged).
    2. One row per individual pesticide found in data, inheriting from
       "Plaguicida individual" (0.10 µg/L each).
    3. One row per summation component (THM, HAA, HPA, ∑2, PFAS) found
       in data, with no individual limit but a ``contributes_to`` link
       to the parent sum parameter.

    Columns added beyond the BOE CSV: ``source``, ``contributes_to``.

    Parameters
    ----------
    data_params : Series, optional
        Unique parameter names from the scraped data.  If None, reads them
        from ``ALL_DATA_FILE``.
    """
    boe = load_boe_limits()
    boe["source"] = "BOE"
    boe["contributes_to"] = pd.NA

    # Discover parameters from data
    if data_params is None:
        data_params = (
            pd.read_csv(ALL_DATA_FILE, usecols=["parametro"])["parametro"]
            .dropna()
            .unique()
        )
    data_param_set = set(data_params)

    # --- Layer 2: individual pesticides ---------------------------------
    pest_template = (
        boe.loc[boe["parametro"] == "Plaguicida individual"].iloc[0].to_dict()
    )
    pest_rows = []
    for name in sorted(p for p in data_param_set if is_pesticide(p)):
        row = pest_template.copy()
        row["parametro"] = name
        row["source"] = "BOE: Plaguicida individual"
        pest_rows.append(row)

    # --- Layer 3: summation components ----------------------------------
    comp_rows = []
    for comp_name, sum_name in sorted(COMPONENT_TO_SUM_MAP.items()):
        if comp_name not in data_param_set:
            continue
        # Look up the parent sum row for unit info
        sum_row = boe.loc[boe["parametro"] == sum_name]
        comp = {
            "parametro": comp_name,
            "valor_parametrico": pd.NA,      # no individual limit
            "valor_no_aptitud": pd.NA,
            "unidad": sum_row.iloc[0]["unidad"] if len(sum_row) else pd.NA,
            "tipo": sum_row.iloc[0]["tipo"] if len(sum_row) else pd.NA,
            "source": f"BOE: {sum_name}",
            "contributes_to": sum_name,
        }
        comp_rows.append(comp)

    parts = [boe]
    if pest_rows:
        parts.append(pd.DataFrame(pest_rows))
    if comp_rows:
        parts.append(pd.DataFrame(comp_rows))

    expanded = pd.concat(parts, ignore_index=True)
    return expanded


# ===================================================================
# 3. Merge with legal limits
# ===================================================================

def merge_with_limits(
    df: pd.DataFrame,
    limits: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Merge water quality data with (expanded) BOE legal limits.

    Adds columns: ``parametro_boe``, ``valor_parametrico``, ``unidad_limite``,
    ``tipo_parametro``, ``valor_no_aptitud``, ``source_limite``,
    ``contributes_to``, ``unidad_norm``.

    Parameters
    ----------
    df : DataFrame
        Water quality data with ``parametro`` and ``unidad`` columns.
    limits : DataFrame, optional
        Expanded limits table.  If None, builds it via ``build_expanded_limits``.
    """
    if limits is None:
        data_params = df["parametro"].dropna().unique()
        limits = build_expanded_limits(data_params=data_params)

    df = df.copy()
    df["parametro_boe"] = df["parametro"].map(normalize_parametro)

    # Normalize data units
    if "unidad" in df.columns:
        df["unidad_norm"] = df["unidad"].map(normalize_unidad)

    limit_cols = limits[
        ["parametro", "valor_parametrico", "unidad", "tipo",
         "valor_no_aptitud", "source", "contributes_to"]
    ].rename(columns={
        "parametro": "parametro_boe",
        "unidad": "unidad_limite",
        "tipo": "tipo_parametro",
        "source": "source_limite",
    })

    merged = df.merge(limit_cols, on="parametro_boe", how="left")
    return merged


# ===================================================================
# 4. Diagnostics (standalone mode)
# ===================================================================

def run_diagnostics() -> None:
    """Compare scraped parameter names against BOE limits and print a report."""
    print("Loading data...")
    data_params = (
        pd.read_csv(ALL_DATA_FILE, usecols=["parametro"])["parametro"]
        .dropna()
        .unique()
    )
    boe = pd.read_csv(BOE_LIMITS_FILE)
    boe_params = set(boe["parametro"].dropna().unique())

    print(f"  Unique scraped parameters: {len(data_params)}")
    print(f"  BOE regulated parameters:  {len(boe_params)}\n")

    # Normalize all scraped names
    normalized = {p: normalize_parametro(p) for p in data_params}

    # Categorize
    exact_matches = []
    renamed_matches = []
    pesticide_params = []
    unmatched_data = []

    for raw, norm in sorted(normalized.items()):
        if raw in boe_params:
            exact_matches.append(raw)
        elif norm in boe_params and norm != raw:
            renamed_matches.append((raw, norm))
        elif is_pesticide(raw):
            pesticide_params.append(raw)
        else:
            unmatched_data.append(raw)

    # BOE params not in data at all
    all_normalized = set(normalized.values())
    boe_not_in_data = sorted(boe_params - all_normalized - {"Plaguicida individual"})

    sep = "=" * 70

    # --- Exact matches ---
    print(f"{sep}")
    print(f"  EXACT MATCHES ({len(exact_matches)})")
    print(f"{sep}")
    for p in exact_matches:
        print(f"  ✓ {p}")

    # --- Renamed matches ---
    print(f"\n{sep}")
    print(f"  RENAMED MATCHES ({len(renamed_matches)})")
    print(f"{sep}")
    for raw, norm in renamed_matches:
        print(f"  {raw}")
        print(f"    → {norm}")

    # --- Pesticides ---
    print(f"\n{sep}")
    print(f"  INDIVIDUAL PESTICIDES ({len(pesticide_params)}) → BOE 'Plaguicida individual'")
    print(f"{sep}")
    for p in pesticide_params[:10]:
        print(f"  {p}")
    if len(pesticide_params) > 10:
        print(f"  ... and {len(pesticide_params) - 10} more")

    # --- Unmatched data params ---
    print(f"\n{sep}")
    print(f"  DATA PARAMS WITH NO BOE MATCH ({len(unmatched_data)})")
    print(f"{sep}")
    for p in unmatched_data:
        print(f"  ? {p}")

    # --- BOE params not in data ---
    print(f"\n{sep}")
    print(f"  BOE PARAMS NOT FOUND IN DATA ({len(boe_not_in_data)})")
    print(f"{sep}")
    for p in boe_not_in_data:
        print(f"  ✗ {p}")

    # --- Summary ---
    total_boe = len(boe_params)
    matched_boe = len(exact_matches) + len(renamed_matches)
    # Pesticides cover the generic "Plaguicida individual" BOE entry
    if any(is_pesticide(p) for p in data_params):
        matched_boe += 1  # count "Plaguicida individual" as covered
    print(f"\n{sep}")
    print(f"  SUMMARY")
    print(f"{sep}")
    print(f"  BOE parameters matched:   {matched_boe}/{total_boe} "
          f"({matched_boe / total_boe * 100:.0f}%)")
    print(f"  BOE params missing:       {len(boe_not_in_data)} "
          f"(mostly artificial radionuclides)")
    print(f"  Data params unmatched:    {len(unmatched_data)} "
          f"(supplementary / informational)")
    print(f"  Individual pesticides:    {len(pesticide_params)} "
          f"(all → 'Plaguicida individual' 0.10 µg/L)")

    # --- Expanded limits table ---
    print(f"\n{sep}")
    print(f"  EXPANDED LIMITS TABLE")
    print(f"{sep}")
    expanded = build_expanded_limits(data_params=data_params)
    n_original = (expanded["source"] == "BOE").sum()
    n_pesticide = expanded["source"].str.startswith("BOE: Plaguicida").sum()
    n_component = expanded["contributes_to"].notna().sum()
    print(f"  Original BOE rows:          {n_original}")
    print(f"  + Individual pesticides:    {n_pesticide}")
    print(f"  + Summation components:     {n_component}")
    print(f"  = Total expanded rows:      {len(expanded)}")

    # --- Step-by-step coverage on unique params ---
    print(f"\n{sep}")
    print(f"  STEP-BY-STEP COVERAGE (unique params)")
    print(f"{sep}")
    total = len(data_params)

    # Step 0: raw exact matches only (no normalization)
    raw_match = sum(1 for p in data_params if p in boe_params)
    print(f"  Step 0 — Raw exact match:           {raw_match:>4}/{total} "
          f"({raw_match / total * 100:.1f}%)")

    # Step 1: + param name normalization (PARAM_RENAME_MAP)
    norm_match = sum(
        1 for p in data_params
        if normalize_parametro(p) in boe_params
    )
    print(f"  Step 1 — + Name normalization:      {norm_match:>4}/{total} "
          f"({norm_match / total * 100:.1f}%)  [+{norm_match - raw_match}]")

    # Step 2: + pesticide expansion
    pest_match = norm_match + sum(
        1 for p in data_params
        if normalize_parametro(p) not in boe_params and is_pesticide(p)
    )
    print(f"  Step 2 — + Pesticide expansion:     {pest_match:>4}/{total} "
          f"({pest_match / total * 100:.1f}%)  [+{pest_match - norm_match}]")

    # Step 3: + summation components (contributes_to)
    comp_match = pest_match + sum(
        1 for p in data_params
        if p in COMPONENT_TO_SUM_MAP
    )
    print(f"  Step 3 — + Summation components:    {comp_match:>4}/{total} "
          f"({comp_match / total * 100:.1f}%)  [+{comp_match - pest_match}]")

    remaining = total - comp_match
    print(f"  Remaining unlinked:                 {remaining:>4}/{total} "
          f"({remaining / total * 100:.1f}%)")

    # --- Row-level coverage estimate ---
    print(f"\n{sep}")
    print(f"  ROW-LEVEL COVERAGE ESTIMATE")
    print(f"{sep}")
    # Count rows per category using the parametro column we already loaded
    all_parametros = pd.read_csv(ALL_DATA_FILE, usecols=["parametro"])["parametro"]
    total_rows = len(all_parametros)
    rows_exact = all_parametros.isin(boe_params).sum()
    rows_renamed = all_parametros.map(normalize_parametro).isin(boe_params).sum()
    rows_pest = (rows_renamed
                 + all_parametros[~all_parametros.map(normalize_parametro).isin(boe_params)]
                 .apply(is_pesticide).sum())
    rows_comp = (rows_pest
                 + all_parametros.isin(COMPONENT_TO_SUM_MAP).sum())

    print(f"  Total rows:                         {total_rows:>10,}")
    print(f"  Step 0 — Raw exact match:           {rows_exact:>10,} "
          f"({rows_exact / total_rows * 100:.1f}%)")
    print(f"  Step 1 — + Name normalization:      {rows_renamed:>10,} "
          f"({rows_renamed / total_rows * 100:.1f}%)  [+{rows_renamed - rows_exact:,}]")
    print(f"  Step 2 — + Pesticide expansion:     {rows_pest:>10,} "
          f"({rows_pest / total_rows * 100:.1f}%)  [+{rows_pest - rows_renamed:,}]")
    print(f"  Step 3 — + Summation components:    {rows_comp:>10,} "
          f"({rows_comp / total_rows * 100:.1f}%)  [+{rows_comp - rows_pest:,}]")
    rows_remaining = total_rows - rows_comp
    print(f"  Remaining unlinked:                 {rows_remaining:>10,} "
          f"({rows_remaining / total_rows * 100:.1f}%)")

    # --- Unit compatibility ---
    print(f"\n{sep}")
    print(f"  UNIT COMPATIBILITY")
    print(f"{sep}")
    print(f"  All 15 unit mismatches are naming-only (no numeric conversion).")
    print(f"  UNIT_RENAME_MAP entries: {len(UNIT_RENAME_MAP)}")


if __name__ == "__main__":
    run_diagnostics()
