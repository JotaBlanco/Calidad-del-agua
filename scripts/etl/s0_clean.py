"""
Step 0: Basic type cleaning.

Parses raw string columns into proper types for downstream use:
  - ``valor_num``  — numeric version of ``valor`` (float, NaN if unparseable)
  - ``fecha``      — parsed datetime of ``fecha_toma``
  - ``año``        — year extracted from ``fecha``
  - ``trimestre``  — quarter string like "2024-Q1"

Also repairs the mis-parsed sum rows described in :func:`repair_sum_rows`.
"""

from __future__ import annotations

import re

import pandas as pd

DATE_FMT = "%d/%m/%Y %H:%M"

# A bulletin line for a sum parameter reads "Suma 20 PFAs µg/L", the family size
# being part of the parameter's name.  When the lab reported no result, the PDF
# parser used to read that size as the measurement and the family name as part
# of the unit, yielding parametro="Suma", valor=20, unidad="PFAs µg/L" — a
# fabricated concentration for a parameter nobody measured.  The parser no
# longer does that (scraper/scrape.py), but ~13k rows scraped before the fix are
# already in data/raw/csvs/, which is a cache we do not rewrite, so they are
# repaired on the way in.
_SUM_UNIT = re.compile(r"^(?P<familia>.*?)\s+(?P<unidad>\S+)$")


def repair_sum_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Put the family size back in the name and blank out the fake value.

    Leaves the row in place, matching the shape the same measurement has when it
    parsed correctly elsewhere in the dataset (``parametro="Suma 20 PFAs"``,
    empty ``valor``): the bulletin did cover PFAS, and that it came back without
    a result is worth keeping.

    Modifies ``df`` in place — ``clean_types`` has already copied it.
    """
    if not {"parametro", "valor", "unidad"} <= set(df.columns):
        return df

    broken = df["parametro"].eq("Suma")
    if not broken.any():
        return df

    partes = df.loc[broken, "unidad"].astype(str).str.extract(_SUM_UNIT)
    tamaño = pd.to_numeric(df.loc[broken, "valor"], errors="coerce")

    # Without both halves there is nothing to rebuild the name from; such a row
    # keeps its useless value rather than growing a half-written name.  Same for
    # the handful whose family ends in a number ("… (HPA) 5.0E-4 µg/L"): that
    # tail may well be the result, and guessing would reintroduce exactly the
    # invented measurement this function exists to remove.
    ok = (
        partes["familia"].notna()
        & partes["unidad"].notna()
        & tamaño.notna()
        & ~partes["familia"].str.contains(r"[\d.,]$", regex=True, na=False)
    )
    idx = df.index[broken][ok]

    df.loc[idx, "parametro"] = (
        "Suma " + tamaño[ok].astype(int).astype(str) + " " + partes.loc[ok, "familia"]
    )
    df.loc[idx, "unidad"] = partes.loc[ok, "unidad"]
    df.loc[idx, "valor"] = None
    return df


def clean_types(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``valor_num`` and ``fecha`` columns.

    Parameters
    ----------
    df : DataFrame
        Must contain ``valor`` and ``fecha_toma``.
    """
    df = repair_sum_rows(df.copy())
    df["valor_num"] = pd.to_numeric(df["valor"], errors="coerce")
    df["fecha"] = pd.to_datetime(
        df["fecha_toma"], format="mixed", dayfirst=True, errors="coerce"
    )
    df["año"] = df["fecha"].dt.year.astype("Int64")
    df["trimestre"] = df["fecha"].dt.to_period("Q").astype(str)
    return df
