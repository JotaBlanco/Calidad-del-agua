"""
Step 6: Enrich with municipality geolocation.

Left-joins geocoded municipality coordinates (lat/lon) onto the data
using ``municipio_code`` as the join key.

Adds columns: ``lat_municipio``, ``lon_municipio``.
"""

from __future__ import annotations

import pandas as pd

from . import DATA_DIR

GEOCODED_FILE = DATA_DIR / "processed" / "geocoded_municipalities.csv"


def add_geo(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``lat_municipio`` and ``lon_municipio`` from geocoded data.

    Parameters
    ----------
    df : DataFrame
        Must contain ``municipio_code``.
    """
    geo = pd.read_csv(GEOCODED_FILE, usecols=["municipio_code", "lat_municipio", "lon_municipio"])
    geo["municipio_code"] = geo["municipio_code"].astype(str)

    df = df.copy()
    df["municipio_code"] = df["municipio_code"].astype(str)

    df = df.merge(geo, on="municipio_code", how="left")
    return df
