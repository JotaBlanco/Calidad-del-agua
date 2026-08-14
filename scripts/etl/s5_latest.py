"""
Step 5: Tag the rows that represent the current state of each sampling point.

Two "latest" flags, grouping along different axes:

- ``es_ultima_toma_del_tipo``       — grouped by point × ``tipo_analisis``.
  True for rows belonging to the most recent bulletin of each analysis type.
- ``es_ultimo_valor_del_parametro`` — grouped by point × ``parametro``.
  True for the most recent value of each individual parameter, whichever
  bulletin it came from, even when that bulletin is not the point's latest.

``tipo_analisis`` is deliberately absent from the second grouping.  A control
analysis measures a handful of parameters and a complete one measures
everything, so keeping the analysis type in the key returns *one row per
parameter per analysis type* — the same parameter twice or three times over,
each with its own date.  That double-counted the dashboard's parameter tables
and let a superseded incumplimiento keep a point flagged even after a newer,
compliant reading of the same parameter arrived under a different analysis
type.  Grouping by parameter alone still preserves parameters that only the
complete analysis covers: they are simply the latest value of their own group.

Consequence of dropping it: the flags are no longer nested.  A row can be the
latest of its bulletin type without being the latest value of its parameter
(a later bulletin of another type re-measured it).

``mediciones_vigentes()`` below is the single source of truth for "what counts
as the current state of the water", and every consumer should go through it
rather than filtering on a flag directly — that divergence is exactly what
made the aggregates and the dashboard disagree in the past.

Also adds ``antiguedad_dias``: days elapsed since the sample was taken.

(``tipo_boletin`` is omitted from the grouping because it is a parent category
of ``tipo_analisis`` and therefore redundant.)
"""

from __future__ import annotations

import pandas as pd

# What identifies a sampling point.
POINT_ID_COLS: list[str] = [
    "ccaa_code",
    "provincia_code",
    "municipio_code",
    "red",
    "punto_muestreo",
]

# Point + analysis type: the bulletin-level grouping.
ANALYSIS_GROUP_COLS: list[str] = POINT_ID_COLS + ["tipo_analisis"]

# Point + parameter: the parameter-level grouping.  No ``tipo_analisis`` —
# see the module docstring.
PARAMETER_GROUP_COLS: list[str] = POINT_ID_COLS + ["parametro"]

# Backwards-compatible alias for the original constant name.
LATEST_GROUP_COLS = ANALYSIS_GROUP_COLS

DATE_COL = "fecha_toma"

# ---------------------------------------------------------------------------
# Freshness policy
# ---------------------------------------------------------------------------
# Maximum age, in days, for a measurement to count towards a point's current
# state.  ``None`` means no limit.
#
# Deliberately unlimited: a bad result that was never re-tested is itself a
# finding — it signals both a water-quality problem and a monitoring-frequency
# one (RD 3/2023 sets minimum sampling frequencies by supplied volume).  Ageing
# it out would hide both.  Set an integer here to switch the policy on
# globally, or pass ``max_antiguedad_dias`` per call.
MAX_ANTIGUEDAD_DIAS: int | None = None


# ---------------------------------------------------------------------------
# Deprecated column aliases
# ---------------------------------------------------------------------------
# The flags used to be called ``es_ultimo_analisis`` and ``es_ultima_medicion``.
# Both names hid the grouping key, and the first one especially reads as "the
# point's latest analysis" when it actually means "the latest of each analysis
# type" — which is how the aggregates and the dashboard ended up filtering on
# different flags without anyone noticing.
#
# The old names are still emitted as copies so that already-published datasets
# (Kaggle) stay readable by existing consumers.  Nothing in this repository
# reads them.  Delete this map and the ``include_deprecated_aliases`` argument
# once downstream users have migrated.
DEPRECATED_FLAG_ALIASES: dict[str, str] = {
    "es_ultimo_analisis": "es_ultima_toma_del_tipo",
    "es_ultima_medicion": "es_ultimo_valor_del_parametro",
}


def _es_ultimo_por(
    fecha: pd.Series, df: pd.DataFrame, group_cols: list[str]
) -> pd.Series:
    """True where ``fecha`` equals the maximum date within its group."""
    return fecha == fecha.groupby([df[c] for c in group_cols]).transform("max")


def tag_latest(
    df: pd.DataFrame,
    ref_date: pd.Timestamp | None = None,
    include_deprecated_aliases: bool = True,
) -> pd.DataFrame:
    """Add the two "latest" flags and ``antiguedad_dias``.

    Parameters
    ----------
    df : DataFrame
        Must contain the grouping columns, ``parametro`` and ``fecha_toma``.
    ref_date : Timestamp, optional
        Date that ``antiguedad_dias`` is measured against.  Defaults to the
        newest date in the dataset, which keeps the column deterministic for a
        given input.  Pass ``pd.Timestamp.utcnow().normalize()`` if you want
        ages relative to today instead — note that the newest date in the data
        can lag today by months between scrapes.
    include_deprecated_aliases : bool, default True
        Also emit the pre-rename column names (see
        :data:`DEPRECATED_FLAG_ALIASES`) so published datasets stay
        backwards-compatible.
    """
    df = df.copy()

    # Use the pre-parsed fecha from s0 if available, otherwise parse here
    if "fecha" in df.columns and pd.api.types.is_datetime64_any_dtype(df["fecha"]):
        fecha = df["fecha"]
    else:
        fecha = pd.to_datetime(
            df[DATE_COL], format="mixed", dayfirst=True, errors="coerce"
        )

    df["es_ultima_toma_del_tipo"] = _es_ultimo_por(fecha, df, ANALYSIS_GROUP_COLS)
    df["es_ultimo_valor_del_parametro"] = _es_ultimo_por(
        fecha, df, PARAMETER_GROUP_COLS
    )

    if ref_date is None:
        ref_date = fecha.max()
    df["antiguedad_dias"] = (ref_date - fecha).dt.days

    if include_deprecated_aliases:
        for old, new in DEPRECATED_FLAG_ALIASES.items():
            df[old] = df[new]

    return df


def mediciones_vigentes(
    df: pd.DataFrame,
    max_antiguedad_dias: int | None = MAX_ANTIGUEDAD_DIAS,
) -> pd.DataFrame:
    """Return the rows that represent each point's current state.

    That is the most recent value of every parameter — including parameters
    whose latest reading predates the point's most recent bulletin, because a
    result that was never repeated does not stop being relevant.

    Parameters
    ----------
    df : DataFrame
        Output of :func:`tag_latest`.
    max_antiguedad_dias : int, optional
        Drop measurements older than this many days.  Defaults to
        ``MAX_ANTIGUEDAD_DIAS`` (no limit).  Rows with an unparseable date have
        no age and are dropped whenever a limit is in force.
    """
    mask = df["es_ultimo_valor_del_parametro"]
    if max_antiguedad_dias is not None:
        mask = mask & (df["antiguedad_dias"] <= max_antiguedad_dias)
    return df[mask]
