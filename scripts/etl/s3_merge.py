"""
Step 3: Merge water quality data with legal limits.

Left-joins the scraped data against the expanded BOE limits table,
adding limit values, unit info, and compliance metadata to each row.
"""

from __future__ import annotations

import pandas as pd

from .s1_normalization import normalize_parametro, normalize_unidad
from .s2_limits_expansion import build_expanded_limits


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
