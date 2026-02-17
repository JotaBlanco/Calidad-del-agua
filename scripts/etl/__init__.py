"""
ETL package for water quality data normalization and enrichment.

Modules:
    s1_normalization      — Parameter name and unit normalization
    s2_limits_expansion   — Expanded BOE limits (+ pesticides, + sum components)
    s3_merge              — Merge water quality data with legal limits
    s4_compliance         — Compliance checking (aptitud / aptitud_suma)

Usage:
    from scripts.etl import normalize_parametro, build_expanded_limits, merge_with_limits
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Shared paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
LIMITS_DIR = DATA_DIR / "limits"

ALL_DATA_FILE = PROCESSED_DIR / "all_data.csv"
BOE_LIMITS_FILE = LIMITS_DIR / "limites_legales_boe.csv"

# ---------------------------------------------------------------------------
# Re-exports for convenience
# ---------------------------------------------------------------------------
from .s1_normalization import (  # noqa: E402
    PARAM_RENAME_MAP,
    PESTICIDE_PREFIXES,
    COMPONENT_TO_SUM_MAP,
    UNIT_RENAME_MAP,
    normalize_parametro,
    normalize_unidad,
    is_pesticide,
)
from .s2_limits_expansion import (  # noqa: E402
    load_boe_limits,
    build_expanded_limits,
)
from .s3_merge import merge_with_limits  # noqa: E402
from .s4_compliance import add_compliance, MEASUREMENT_ID_COLS  # noqa: E402
