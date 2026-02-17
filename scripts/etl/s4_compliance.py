"""
Step 4: Compliance checking against BOE legal limits.

Adds an ``aptitud`` column with three possible values:
  - ``apta``         — value ≤ valor_parametrico (within legal limit)
  - ``incumple_vp``  — valor_parametrico < value ≤ valor_no_aptitud
                       (Part C indicator params only; corrective action needed,
                        but Art. 6.2 says this does NOT presume "no apta")
  - ``no_apta``      — value > valor_no_aptitud  (Part C), or
                        value > valor_parametrico (Parts A/B/D/E/F, no VNA)

Special-case parameters:
  - pH:                  range check  (VP 6.5–9.5, VNA <4.5 / >10.0)
  - Índice de Langelier:  range check  (VP ±0.5, no VNA)

Summation compliance (``aptitud_suma``):
  For parameters with ``contributes_to`` (THM, HAA, HPA, ∑2, PFAS),
  groups rows by measurement ID + parent sum parameter, sums the
  individual component values, and compares against the parent's VP.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── Measurement-ID columns ────────────────────────────────────────────
MEASUREMENT_ID_COLS: list[str] = [
    "ccaa_code",
    "provincia_code",
    "municipio_code",
    "red",
    "punto_muestreo",
    "fecha_toma",
]


# ── Individual compliance ─────────────────────────────────────────────

def _parse_valor(series: pd.Series) -> pd.Series:
    """Coerce a column to numeric, returning NaN for non-numeric strings."""
    return pd.to_numeric(series, errors="coerce")


def _check_ph(value: pd.Series, vp_str: str, vna_str: str) -> pd.Series:
    """Classify pH measurements against range-based limits.

    VP: 6.5 a 9.5  →  apta if 6.5 ≤ value ≤ 9.5
    VNA: <4.5 o >10.0  →  no_apta if value < 4.5 or value > 10.0
    Otherwise: incumple_vp
    """
    val = _parse_valor(value)
    result = pd.Series(np.nan, index=value.index, dtype="object")

    apta = (val >= 6.5) & (val <= 9.5)
    no_apta = (val < 4.5) | (val > 10.0)
    incumple = ~apta & ~no_apta

    result[apta] = "apta"
    result[no_apta] = "no_apta"
    result[incumple] = "incumple_vp"
    return result


def _check_langelier(value: pd.Series) -> pd.Series:
    """Classify Índice de Langelier against ±0.5 range.

    VP: +/- 0.5  →  apta if -0.5 ≤ value ≤ +0.5
    No VNA exists  →  incumple_vp otherwise
    (There's no "no_apta" threshold for Langelier.)
    """
    val = _parse_valor(value)
    result = pd.Series(np.nan, index=value.index, dtype="object")

    apta = (val >= -0.5) & (val <= 0.5)
    result[apta] = "apta"
    result[~apta & val.notna()] = "incumple_vp"
    return result


def classify_individual(df: pd.DataFrame) -> pd.Series:
    """Compute per-row ``aptitud`` based on value vs. VP and VNA.

    Expects columns: ``valor``, ``valor_parametrico``, ``valor_no_aptitud``,
    ``parametro_boe``.

    Returns a Series aligned to ``df.index``.
    """
    aptitud = pd.Series(np.nan, index=df.index, dtype="object")

    # ── Special cases ─────────────────────────────────────────────
    is_ph = df["parametro_boe"] == "pH"
    if is_ph.any():
        aptitud[is_ph] = _check_ph(df.loc[is_ph, "valor"], "6.5 a 9.5", "<4.5 o >10.0")

    is_lang = df["parametro_boe"] == "Índice de Langelier"
    if is_lang.any():
        aptitud[is_lang] = _check_langelier(df.loc[is_lang, "valor"])

    # ── General case (all other parameters) ───────────────────────
    general = ~is_ph & ~is_lang & df["valor_parametrico"].notna()
    idx = df.index[general]

    val = _parse_valor(df.loc[idx, "valor"]).fillna(0)
    vp = _parse_valor(df.loc[idx, "valor_parametrico"])
    vna = _parse_valor(df.loc[idx, "valor_no_aptitud"])

    has_vna = vna.notna()
    apta = val <= vp
    exceeds_vp = val > vp

    # apta: value ≤ VP
    aptitud.loc[idx[apta]] = "apta"

    # With VNA (Part C indicators): VP < value ≤ VNA → incumple_vp
    aptitud.loc[idx[exceeds_vp & has_vna & (val <= vna)]] = "incumple_vp"

    # With VNA: value > VNA → no_apta
    aptitud.loc[idx[exceeds_vp & has_vna & (val > vna)]] = "no_apta"

    # Without VNA (Parts A/B/D/E/F): exceeding VP → no_apta directly
    aptitud.loc[idx[exceeds_vp & ~has_vna]] = "no_apta"

    return aptitud


# ── Summation compliance ──────────────────────────────────────────────

def classify_summation(df: pd.DataFrame) -> pd.Series:
    """Compute ``aptitud_suma`` for summation-component rows.

    Groups by measurement ID + ``contributes_to``, sums numeric ``valor``,
    and compares against the parent sum parameter's ``valor_parametrico``
    (looked up from the merged limit columns already on ``df``).

    Only rows where ``contributes_to`` is not null are affected;
    all other rows get NaN.
    """
    aptitud_suma = pd.Series(np.nan, index=df.index, dtype="object")

    has_sum = df["contributes_to"].notna()
    if not has_sum.any():
        return aptitud_suma

    sub = df.loc[has_sum].copy()
    sub["valor_num"] = _parse_valor(sub["valor"]).fillna(0)

    group_cols = MEASUREMENT_ID_COLS + ["contributes_to"]

    # Sum component values per measurement + parent sum
    sums = (
        sub.groupby(group_cols, dropna=False)["valor_num"]
        .sum()
        .reset_index()
        .rename(columns={"valor_num": "suma_valor"})
    )

    # Look up the parent sum's VP from the limits already on the DataFrame.
    # The parent sum parameter itself appears in rows where parametro_boe == contributes_to.
    # We take the VP from the expanded limits (already merged onto df).
    parent_vp = (
        df.loc[df["parametro_boe"].isin(df["contributes_to"].dropna().unique())]
        .drop_duplicates(subset=["parametro_boe"])
        [["parametro_boe", "valor_parametrico"]]
        .rename(columns={"parametro_boe": "contributes_to",
                         "valor_parametrico": "vp_suma"})
    )
    parent_vp["vp_suma"] = pd.to_numeric(parent_vp["vp_suma"], errors="coerce")

    sums = sums.merge(parent_vp, on="contributes_to", how="left")
    sums["_aptitud_suma"] = np.where(
        sums["suma_valor"] <= sums["vp_suma"], "apta", "no_apta"
    )
    sums.loc[sums["vp_suma"].isna(), "_aptitud_suma"] = np.nan

    # Map back to original rows
    sub = sub.merge(sums[group_cols + ["_aptitud_suma"]], on=group_cols, how="left")
    aptitud_suma.loc[sub.index] = sub["_aptitud_suma"].values

    return aptitud_suma


# ── Public entry point ────────────────────────────────────────────────

def add_compliance(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``aptitud`` and ``aptitud_suma`` columns to a merged DataFrame.

    Parameters
    ----------
    df : DataFrame
        Output of ``merge_with_limits`` (must contain ``valor``,
        ``valor_parametrico``, ``valor_no_aptitud``, ``parametro_boe``,
        ``contributes_to``, and the measurement-ID columns).
    """
    df = df.copy()
    df["aptitud"] = classify_individual(df)
    df["aptitud_suma"] = classify_summation(df)

    # For summation components (no individual VP), use the sum-level result
    fill_mask = df["aptitud"].isna() & df["aptitud_suma"].notna()
    df.loc[fill_mask, "aptitud"] = df.loc[fill_mask, "aptitud_suma"]

    return df
