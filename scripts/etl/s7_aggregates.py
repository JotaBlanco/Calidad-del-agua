"""
Step 7: Generate aggregated metric CSVs at multiple geographic levels.

Produces one CSV per level, each containing compliance, risk, pesticide,
freshness, and official-classification metrics computed on the rows returned
by ``mediciones_vigentes()`` — the latest value of every parameter.

Levels:
    ccaa, provincia, municipio, municipio+red, municipio+red+punto_muestreo

Output directory: ``data/processed/aggregates/``
"""

from __future__ import annotations

import pandas as pd

from . import DATA_DIR, PROCESSED_DIR
from .s5_latest import mediciones_vigentes

LOCATIONS_FILE = DATA_DIR / "locations_catalog.csv"
OUTPUT_DIR = PROCESSED_DIR / "aggregates"

LEVELS: dict[str, list[str]] = {
    "ccaa": ["ccaa_code"],
    "provincia": ["ccaa_code", "provincia_code"],
    "municipio": ["ccaa_code", "provincia_code", "municipio_code"],
    "municipio_red": ["ccaa_code", "provincia_code", "municipio_code", "red"],
    "punto_muestreo": [
        "ccaa_code", "provincia_code", "municipio_code", "red", "punto_muestreo",
    ],
}

PARTE_MAP: dict[str, str] = {
    "A – Microbiológicos": "microbiologico",
    "B – Químicos": "quimico",
    "C – Indicadores": "indicador",
    "E – Radiactivos": "radiactivo",
}


# ── helpers ──────────────────────────────────────────────────────────

def _join_names(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Attach human-readable names from the locations catalog."""
    locs = pd.read_csv(LOCATIONS_FILE)
    for col in ("ccaa_code", "provincia_code", "municipio_code"):
        if col in locs.columns:
            locs[col] = locs[col].astype(str)

    if "ccaa_code" in group_cols:
        ccaa = locs[["ccaa_code", "ccaa_name"]].drop_duplicates()
        df = df.merge(ccaa, on="ccaa_code", how="left")
    if "provincia_code" in group_cols:
        prov = locs[["provincia_code", "provincia_name"]].drop_duplicates()
        df = df.merge(prov, on="provincia_code", how="left")
    if "municipio_code" in group_cols:
        muni = locs[["municipio_code", "municipio_name"]].drop_duplicates()
        df = df.merge(muni, on="municipio_code", how="left")
    return df


# ── per-level aggregation ────────────────────────────────────────────

def _aggregate_level(latest: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Compute all approved metrics for one geographic level."""
    g = latest.groupby(group_cols)

    # ── Core compliance ──────────────────────────────────────────
    core = g.agg(
        n_mediciones=("_eval", "size"),
        n_evaluadas=("_eval", "sum"),
        n_apta=("_apta", "sum"),
        n_incumple_vp=("_incumple", "sum"),
        n_no_apta=("_no_apta", "sum"),
    )
    core["pct_apta"] = (core["n_apta"] / core["n_evaluadas"] * 100).round(2)
    core["pct_incumple_vp"] = (core["n_incumple_vp"] / core["n_evaluadas"] * 100).round(2)
    core["pct_no_apta"] = (core["n_no_apta"] / core["n_evaluadas"] * 100).round(2)

    result = core

    # ── Risk severity ────────────────────────────────────────────
    # Distinct parameters with at least one no_apta
    no_apta = latest[latest["_no_apta"]]
    if not no_apta.empty:
        result = result.join(
            no_apta.groupby(group_cols)["parametro_boe"]
            .nunique().rename("n_parametros_no_aptos")
        )
    if "n_parametros_no_aptos" not in result.columns:
        result["n_parametros_no_aptos"] = 0
    result["n_parametros_no_aptos"] = result["n_parametros_no_aptos"].fillna(0).astype(int)

    # Parameter with highest pct_valor_vp
    valid_pct = latest.dropna(subset=["pct_valor_vp"])
    if not valid_pct.empty:
        idx_max = valid_pct.groupby(group_cols)["pct_valor_vp"].idxmax()
        worst = (
            valid_pct.loc[idx_max.values, group_cols + ["parametro_boe"]]
            .set_index(group_cols)
            .rename(columns={"parametro_boe": "parametro_peor"})
        )
        result = result.join(worst)
    else:
        result["parametro_peor"] = None

    # ── Per-part % apta ──────────────────────────────────────────
    for parte_label, suffix in PARTE_MAP.items():
        part = latest[latest["parte_boe"] == parte_label]
        if part.empty:
            result[f"pct_apta_{suffix}"] = None
            continue
        pg = part.groupby(group_cols)
        pa = pg["_apta"].sum()
        pe = pg["_eval"].sum()
        result[f"pct_apta_{suffix}"] = (pa / pe * 100).round(2)

    # ── Pesticides ───────────────────────────────────────────────
    pest = latest[latest["es_plaguicida"]]
    if not pest.empty:
        result = result.join(
            pest.groupby(group_cols)["parametro_boe"]
            .nunique().rename("n_plaguicidas_analizados")
        )
        detected = pest[pest["valor_num"] > 0]
        if not detected.empty:
            result = result.join(
                detected.groupby(group_cols)["parametro_boe"]
                .nunique().rename("n_plaguicidas_detectados")
            )
        pest_bad = pest[pest["_no_apta"]]
        if not pest_bad.empty:
            result = result.join(
                pest_bad.groupby(group_cols)["parametro_boe"]
                .nunique().rename("n_plaguicidas_no_aptos")
            )
    for c in ("n_plaguicidas_analizados", "n_plaguicidas_detectados", "n_plaguicidas_no_aptos"):
        if c not in result.columns:
            result[c] = 0
        result[c] = result[c].fillna(0).astype(int)

    # ── Freshness ────────────────────────────────────────────────
    result = result.join(g["fecha"].max().rename("fecha_ultimo_analisis"))

    # ── Official classification (boletin level) ──────────────────
    bol = latest.drop_duplicates(subset=group_cols + ["boletin_id"])
    bg = bol.groupby(group_cols)
    bol_agg = bg.agg(
        n_boletines=("boletin_id", "size"),
        n_boletines_apto=("_cal_apto", "sum"),
        n_boletines_no_apto=("_cal_no_apto", "sum"),
    )
    bol_agg["pct_boletines_apto"] = (
        bol_agg["n_boletines_apto"] / bol_agg["n_boletines"] * 100
    ).round(2)
    result = result.join(bol_agg)

    return result.reset_index()


# ── Public entry point ───────────────────────────────────────────────

def build_aggregates(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build aggregate CSVs at each geographic level.

    Parameters
    ----------
    df : DataFrame
        Fully enriched DataFrame (output of the main ETL pipeline).
    """
    latest = mediciones_vigentes(df).copy()

    # Pre-compute boolean helpers
    latest["_apta"] = latest["aptitud"] == "apta"
    latest["_incumple"] = latest["aptitud"] == "incumple_vp"
    latest["_no_apta"] = latest["aptitud"] == "no_apta"
    latest["_eval"] = latest["aptitud"].notna()
    latest["_cal_apto"] = latest["calificacion"] == "AGUA APTA PARA EL CONSUMO"
    latest["_cal_no_apto"] = latest["calificacion"] == "AGUA NO APTA PARA EL CONSUMO"

    # Ensure code columns are strings for consistent grouping/joining
    for col in ("ccaa_code", "provincia_code", "municipio_code"):
        latest[col] = latest[col].astype(str)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = {}
    for level_name, group_cols in LEVELS.items():
        agg = _aggregate_level(latest, group_cols)
        agg = _join_names(agg, group_cols)
        out = OUTPUT_DIR / f"agg_{level_name}.csv"
        agg.to_csv(out, index=False)
        results[level_name] = agg
        print(f"  {level_name:20s}: {len(agg):>6,} rows → {out.name}")

    return results
