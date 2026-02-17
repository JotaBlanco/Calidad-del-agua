"""
Step 2: Expanded BOE limits table.

Builds an expanded limits table with three layers:
  1. Original BOE rows (82 parameters)
  2. One row per individual pesticide (inheriting from "Plaguicida individual")
  3. One row per summation component (THM, HAA, HPA, ∑2, PFAS) with contributes_to link
"""

from __future__ import annotations

import pandas as pd

from . import ALL_DATA_FILE, BOE_LIMITS_FILE
from .s1_normalization import COMPONENT_TO_SUM_MAP, is_pesticide


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
        sum_row = boe.loc[boe["parametro"] == sum_name]
        comp = {
            "parametro": comp_name,
            "valor_parametrico": pd.NA,
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
