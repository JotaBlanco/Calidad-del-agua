"""
Step 5: Tag rows belonging to the latest measurement.

Adds two boolean columns:

- ``es_ultimo_analisis``  — True when ``fecha_toma`` equals the most recent
  date for the sampling-point + analysis-type group.
- ``es_ultima_medicion``  — True when ``fecha_toma`` equals the most recent
  date for the sampling-point + analysis-type + parameter group.

Grouping keys:
    analisis:  [ccaa_code, provincia_code, municipio_code, red,
                punto_muestreo, tipo_analisis]
    medicion:  [...same...] + [parametro]

(``tipo_boletin`` is omitted because it is a parent category of
``tipo_analisis`` and therefore redundant for grouping.)
"""

from __future__ import annotations

import pandas as pd

LATEST_GROUP_COLS: list[str] = [
    "ccaa_code",
    "provincia_code",
    "municipio_code",
    "red",
    "punto_muestreo",
    "tipo_analisis",
]

DATE_COL = "fecha_toma"
DATE_FMT = "%d/%m/%Y %H:%M"


def tag_latest(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``es_ultimo_analisis`` and ``es_ultima_medicion`` columns.

    Parameters
    ----------
    df : DataFrame
        Must contain the grouping columns, ``parametro``, and ``fecha_toma``.
    """
    df = df.copy()

    fecha = pd.to_datetime(df[DATE_COL], format=DATE_FMT, errors="coerce")

    # Latest analysis per sampling point + analysis type
    max_analisis = fecha.groupby(
        [df[c] for c in LATEST_GROUP_COLS]
    ).transform("max")
    df["es_ultimo_analisis"] = fecha == max_analisis

    # Latest measurement per sampling point + analysis type + parameter
    max_medicion = fecha.groupby(
        [df[c] for c in LATEST_GROUP_COLS] + [df["parametro"]]
    ).transform("max")
    df["es_ultima_medicion"] = fecha == max_medicion

    return df
