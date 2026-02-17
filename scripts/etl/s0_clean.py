"""
Step 0: Basic type cleaning.

Parses raw string columns into proper types for downstream use:
  - ``valor_num``  — numeric version of ``valor`` (float, NaN if unparseable)
  - ``fecha``      — parsed datetime of ``fecha_toma``
"""

from __future__ import annotations

import pandas as pd

DATE_FMT = "%d/%m/%Y %H:%M"


def clean_types(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``valor_num`` and ``fecha`` columns.

    Parameters
    ----------
    df : DataFrame
        Must contain ``valor`` and ``fecha_toma``.
    """
    df = df.copy()
    df["valor_num"] = pd.to_numeric(df["valor"], errors="coerce")
    df["fecha"] = pd.to_datetime(df["fecha_toma"], format=DATE_FMT, errors="coerce")
    return df
