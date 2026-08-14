#!/usr/bin/env python3
"""
Combina todos los CSVs individuales de municipios en un único CSV consolidado
y realiza un análisis exhaustivo de calidad de datos.

Uso:
    python scripts/merge_csvs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from collections import Counter

import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = ROOT / "data" / "raw" / "csvs"
OUTPUT_DIR = ROOT / "data" / "processed"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "all_data.csv"

# ---------------------------------------------------------------------------
# 1. Merge
# ---------------------------------------------------------------------------

def merge_all_csvs() -> pd.DataFrame:
    """Lee todos los CSVs y los concatena en un único DataFrame."""
    # pathlib's glob matches dotfiles, and untarring the macOS-built seed on
    # Linux leaves an AppleDouble `._` twin beside every CSV — 6531 small
    # binary files that would go straight into read_csv().
    files = sorted(p for p in CSV_DIR.glob("*.csv") if not p.name.startswith("."))
    if not files:
        print(f"ERROR: No se encontraron CSVs en {CSV_DIR}")
        sys.exit(1)

    print(f"Encontrados {len(files)} archivos CSV.")

    frames = []
    errores_lectura = []
    for f in files:
        try:
            df = pd.read_csv(f, dtype=str)
            df["_source_file"] = f.name
            frames.append(df)
        except Exception as e:
            errores_lectura.append((f.name, str(e)))

    if errores_lectura:
        print(f"\n  AVISO: {len(errores_lectura)} archivos con error de lectura:")
        for name, err in errores_lectura[:10]:
            print(f"    - {name}: {err}")

    combined = pd.concat(frames, ignore_index=True)
    print(f"Total de filas combinadas: {len(combined):,}")
    print(f"Columnas: {list(combined.columns)}")
    return combined


# ---------------------------------------------------------------------------
# 2. Análisis de calidad
# ---------------------------------------------------------------------------

def analyze_data_quality(df: pd.DataFrame) -> None:
    """Análisis exhaustivo de calidad de datos."""

    sep = "=" * 70

    # --- 2.1 Resumen general ---
    print(f"\n{sep}")
    print("  RESUMEN GENERAL")
    print(sep)
    print(f"  Filas totales:          {len(df):,}")
    print(f"  Columnas:               {len(df.columns)}")
    print(f"  Municipios únicos:      {df['municipio'].nunique():,}")
    print(f"  Puntos de muestreo:     {df['punto_muestreo'].nunique():,}")
    print(f"  Zonas abastecimiento:   {df['zona_abastecimiento'].nunique():,}")
    print(f"  Boletines únicos:       {df['boletin_id'].nunique():,}")
    print(f"  Parámetros medidos:     {df['parametro'].nunique()}")
    print(f"  Laboratorios:           {df['laboratorio'].nunique()}")
    print(f"  Rango de fechas:        {df['fecha_toma'].min()} — {df['fecha_toma'].max()}")

    # --- 2.2 Valores nulos ---
    print(f"\n{sep}")
    print("  VALORES NULOS POR COLUMNA")
    print(sep)
    for col in df.columns:
        if col == "_source_file":
            continue
        n_null = df[col].isna().sum()
        pct = n_null / len(df) * 100
        if n_null > 0:
            print(f"  {col:30s}  {n_null:>10,}  ({pct:.2f}%)")
    if df.drop(columns="_source_file").isna().sum().sum() == 0:
        print("  (ningún valor nulo)")

    # --- 2.3 Filas completamente duplicadas ---
    print(f"\n{sep}")
    print("  FILAS COMPLETAMENTE DUPLICADAS")
    print(sep)
    cols_sin_source = [c for c in df.columns if c != "_source_file"]
    dup_mask = df.duplicated(subset=cols_sin_source, keep=False)
    n_dup_total = dup_mask.sum()
    n_dup_extra = df.duplicated(subset=cols_sin_source, keep="first").sum()
    print(f"  Filas duplicadas (total involucradas):  {n_dup_total:,}")
    print(f"  Filas extra (eliminables):              {n_dup_extra:,}")
    if n_dup_extra > 0:
        print(f"  % del dataset:                          {n_dup_extra / len(df) * 100:.2f}%")
        # Mostrar ejemplos
        dup_examples = df[df.duplicated(subset=cols_sin_source, keep="first")].head(5)
        print("\n  Ejemplos de duplicados:")
        for _, row in dup_examples.iterrows():
            print(f"    boletin={row['boletin_id']}, municipio={row['municipio']}, "
                  f"param={row['parametro']}, fecha={row['fecha_toma']}")

    # --- 2.4 Duplicados por boletín + parámetro (mismo análisis, distinto valor) ---
    print(f"\n{sep}")
    print("  CONFLICTOS: MISMO BOLETÍN + PARÁMETRO CON DISTINTOS VALORES")
    print(sep)
    key_cols = ["boletin_id", "parametro"]
    grouped = df.groupby(key_cols)["valor"].nunique()
    conflictos = grouped[grouped > 1]
    print(f"  Combinaciones boletín+parámetro con >1 valor distinto: {len(conflictos):,}")
    if len(conflictos) > 0:
        print("\n  Ejemplos:")
        for (bid, param), n_vals in conflictos.head(10).items():
            subset = df[(df["boletin_id"] == bid) & (df["parametro"] == param)]
            vals = subset["valor"].unique()
            print(f"    boletin={bid}, param={param}: valores={list(vals)}")

    # --- 2.5 Análisis de nombres de municipios ---
    print(f"\n{sep}")
    print("  ANÁLISIS DE NOMBRES DE MUNICIPIOS")
    print(sep)

    muni_by_code = df.groupby("municipio_code")["municipio"].apply(
        lambda x: sorted(x.unique())
    )
    muni_multi_name = muni_by_code[muni_by_code.apply(len) > 1]
    print(f"  Códigos de municipio con >1 nombre:  {len(muni_multi_name)}")
    if len(muni_multi_name) > 0:
        print("\n  Detalle:")
        for code, names in muni_multi_name.items():
            print(f"    código {code}: {names}")

    # Buscar municipios con el mismo nombre normalizado pero distinto código
    def normalize_name(s):
        s = s.upper().strip()
        s = re.sub(r"[^A-ZÀ-ÿ0-9\s]", "", s)
        s = re.sub(r"\s+", " ", s)
        return s

    muni_unique = df[["municipio_code", "municipio"]].drop_duplicates()
    muni_unique["muni_norm"] = muni_unique["municipio"].apply(normalize_name)
    norm_groups = muni_unique.groupby("muni_norm").filter(lambda x: x["municipio_code"].nunique() > 1)
    if len(norm_groups) > 0:
        print(f"\n  Municipios con mismo nombre normalizado pero distinto código:")
        for name, grp in norm_groups.groupby("muni_norm"):
            codes = grp[["municipio_code", "municipio"]].values.tolist()
            print(f"    '{name}': {codes}")
    else:
        print("  No hay municipios con nombre idéntico normalizado y distinto código.")

    # --- 2.6 Análisis de puntos de muestreo ---
    print(f"\n{sep}")
    print("  ANÁLISIS DE PUNTOS DE MUESTREO")
    print(sep)

    # Puntos con nombres muy similares en el mismo municipio
    puntos_por_muni = df.groupby("municipio_code")["punto_muestreo"].apply(
        lambda x: sorted(x.dropna().unique())
    )
    similar_candidates = []
    for code, puntos in puntos_por_muni.items():
        if len(puntos) <= 1:
            continue
        norms = [normalize_name(p) for p in puntos]
        for i in range(len(norms)):
            for j in range(i + 1, len(norms)):
                # Similitud simple: uno contenido en el otro o diferencia mínima
                if norms[i] in norms[j] or norms[j] in norms[i]:
                    if norms[i] != norms[j]:
                        similar_candidates.append((code, puntos[i], puntos[j]))
                elif len(norms[i]) > 5 and len(norms[j]) > 5:
                    # Levenshtein rápida: prefijo largo en común
                    common = 0
                    for a, b in zip(norms[i], norms[j]):
                        if a == b:
                            common += 1
                        else:
                            break
                    ratio = common / max(len(norms[i]), len(norms[j]))
                    if ratio > 0.7 and norms[i] != norms[j]:
                        similar_candidates.append((code, puntos[i], puntos[j]))

    print(f"  Pares de puntos potencialmente equivalentes (mismos municipio): {len(similar_candidates)}")
    if similar_candidates:
        shown = 0
        for code, p1, p2 in similar_candidates[:20]:
            muni_name = df[df["municipio_code"] == code]["municipio"].iloc[0]
            print(f"    [{code}] {muni_name}:")
            print(f"      «{p1}»  vs  «{p2}»")
            shown += 1
        if len(similar_candidates) > 20:
            print(f"    ... y {len(similar_candidates) - 20} más")

    # --- 2.7 Análisis de zonas de abastecimiento ---
    print(f"\n{sep}")
    print("  ANÁLISIS DE ZONAS DE ABASTECIMIENTO")
    print(sep)

    za_by_muni = df.groupby("municipio_code")["zona_abastecimiento"].nunique()
    multi_za = za_by_muni[za_by_muni > 1]
    print(f"  Municipios con >1 zona de abastecimiento: {len(multi_za)}")
    if len(multi_za) > 0:
        top_multi = multi_za.nlargest(10)
        print("\n  Top 10 municipios con más zonas:")
        for code, n in top_multi.items():
            muni_name = df[df["municipio_code"] == code]["municipio"].iloc[0]
            zones = df[df["municipio_code"] == code]["zona_abastecimiento"].unique()
            print(f"    [{code}] {muni_name}: {n} zonas")
            for z in sorted(z for z in zones if isinstance(z, str))[:5]:
                print(f"        - {z}")
            if len(zones) > 5:
                print(f"        ... y {len(zones) - 5} más")

    # --- 2.8 Análisis de parámetros ---
    print(f"\n{sep}")
    print("  ANÁLISIS DE PARÁMETROS")
    print(sep)

    params = df["parametro"].value_counts()
    print(f"  Parámetros únicos: {len(params)}")
    print(f"\n  Top 20 parámetros más frecuentes:")
    for param, count in params.head(20).items():
        print(f"    {param:45s}  {count:>10,}")

    # Parámetros con nombres similares (posibles duplicados)
    param_list = sorted(params.index.tolist())
    param_norms = {p: normalize_name(p) for p in param_list}
    norm_to_params: dict[str, list] = {}
    for p, n in param_norms.items():
        norm_to_params.setdefault(n, []).append(p)
    param_duplicates = {k: v for k, v in norm_to_params.items() if len(v) > 1}

    if param_duplicates:
        print(f"\n  Parámetros con nombre normalizado idéntico ({len(param_duplicates)} grupos):")
        for norm, names in sorted(param_duplicates.items()):
            counts = [f"{n} ({params[n]:,})" for n in names]
            print(f"    {' | '.join(counts)}")

    # --- 2.9 Análisis de unidades ---
    print(f"\n{sep}")
    print("  ANÁLISIS DE UNIDADES POR PARÁMETRO")
    print(sep)

    units_per_param = df.groupby("parametro")["unidad"].apply(lambda x: sorted(x.unique()))
    multi_unit = units_per_param[units_per_param.apply(len) > 1]
    print(f"  Parámetros con >1 unidad: {len(multi_unit)}")
    if len(multi_unit) > 0:
        for param, units in multi_unit.items():
            counts = []
            for u in units:
                c = len(df[(df["parametro"] == param) & (df["unidad"] == u)])
                counts.append(f"{u} ({c:,})")
            print(f"    {param}: {' | '.join(counts)}")

    # --- 2.10 Análisis de valores ---
    print(f"\n{sep}")
    print("  ANÁLISIS DE VALORES")
    print(sep)

    # Intentar convertir a numérico
    df["_valor_num"] = pd.to_numeric(df["valor"], errors="coerce")
    n_non_numeric = df["_valor_num"].isna().sum() - df["valor"].isna().sum()
    print(f"  Valores no numéricos: {n_non_numeric:,} ({n_non_numeric / len(df) * 100:.2f}%)")

    if n_non_numeric > 0:
        non_num = df[df["_valor_num"].isna() & df["valor"].notna()]["valor"]
        val_counts = non_num.value_counts().head(20)
        print(f"\n  Top valores no numéricos:")
        for val, count in val_counts.items():
            print(f"    «{val}»: {count:,}")

    # Valores negativos
    negatives = df[df["_valor_num"] < 0]
    if len(negatives) > 0:
        print(f"\n  Valores negativos: {len(negatives):,}")
        neg_params = negatives["parametro"].value_counts().head(10)
        for param, count in neg_params.items():
            print(f"    {param}: {count:,}")

    # Valores extremos por parámetro (potenciales outliers)
    print(f"\n  Posibles outliers (valores extremos por parámetro):")
    numeric_df = df[df["_valor_num"].notna()].copy()
    for param in numeric_df["parametro"].unique():
        subset = numeric_df[numeric_df["parametro"] == param]["_valor_num"]
        if len(subset) < 10:
            continue
        q1 = subset.quantile(0.01)
        q99 = subset.quantile(0.99)
        mean = subset.mean()
        std = subset.std()
        if std > 0:
            extreme = subset[(subset < q1) | (subset > q99)]
            if len(extreme) > 0 and subset.max() > mean + 10 * std:
                print(f"    {param}: min={subset.min()}, max={subset.max()}, "
                      f"mean={mean:.2f}, std={std:.2f}, "
                      f"outliers(>10σ)={len(subset[subset > mean + 10 * std])}")

    # --- 2.11 Análisis de calificaciones ---
    print(f"\n{sep}")
    print("  ANÁLISIS DE CALIFICACIONES")
    print(sep)

    calif = df["calificacion"].value_counts()
    for c, count in calif.items():
        print(f"    {c:60s}  {count:>10,}  ({count / len(df) * 100:.1f}%)")

    # --- 2.12 Análisis de fechas ---
    print(f"\n{sep}")
    print("  ANÁLISIS DE FECHAS")
    print(sep)

    df["_fecha_parsed"] = pd.to_datetime(df["fecha_toma"], format="%d/%m/%Y %H:%M", errors="coerce")
    n_fecha_bad = df["_fecha_parsed"].isna().sum()
    print(f"  Fechas no parseables (formato dd/mm/yyyy HH:MM): {n_fecha_bad:,}")

    if n_fecha_bad > 0:
        bad_dates = df[df["_fecha_parsed"].isna()]["fecha_toma"].value_counts().head(10)
        print("  Ejemplos de fechas no parseables:")
        for d, c in bad_dates.items():
            print(f"    «{d}»: {c:,}")

    if df["_fecha_parsed"].notna().any():
        print(f"  Fecha mínima:  {df['_fecha_parsed'].min()}")
        print(f"  Fecha máxima:  {df['_fecha_parsed'].max()}")

        # Distribución por año-mes
        df["_year_month"] = df["_fecha_parsed"].dt.to_period("M")
        monthly = df["_year_month"].value_counts().sort_index()
        print(f"\n  Distribución mensual (últimos 12 meses disponibles):")
        for period, count in monthly.tail(12).items():
            print(f"    {period}: {count:>10,}")

    # --- 2.13 Análisis de redes ---
    print(f"\n{sep}")
    print("  ANÁLISIS DE REDES")
    print(sep)

    redes = df["red"].value_counts()
    print(f"  Redes únicas: {len(redes)}")
    print(f"\n  Top 20 redes más frecuentes:")
    for red, count in redes.head(20).items():
        print(f"    {red:50s}  {count:>10,}")

    # --- 2.14 Análisis de laboratorios ---
    print(f"\n{sep}")
    print("  ANÁLISIS DE LABORATORIOS")
    print(sep)

    labs = df["laboratorio"].value_counts()
    print(f"  Laboratorios únicos: {len(labs)}")
    for lab, count in labs.items():
        print(f"    {lab:60s}  {count:>10,}")

    # --- 2.15 Consistencia de tipos de boletín y análisis ---
    print(f"\n{sep}")
    print("  TIPOS DE BOLETÍN Y ANÁLISIS")
    print(sep)

    tipo_bol = df["tipo_boletin"].value_counts()
    print(f"  Tipos de boletín:")
    for t, c in tipo_bol.items():
        print(f"    {t:40s}  {c:>10,}")

    tipo_an = df["tipo_analisis"].value_counts()
    print(f"\n  Tipos de análisis:")
    for t, c in tipo_an.items():
        print(f"    {t:40s}  {c:>10,}")

    # --- 2.16 Cobertura geográfica ---
    print(f"\n{sep}")
    print("  COBERTURA GEOGRÁFICA")
    print(sep)

    ccaa_counts = df.groupby("ccaa_code")["municipio_code"].nunique()
    prov_counts = df.groupby("provincia_code")["municipio_code"].nunique()
    print(f"  Comunidades autónomas: {df['ccaa_code'].nunique()}")
    print(f"  Provincias:            {df['provincia_code'].nunique()}")
    print(f"\n  Municipios por provincia:")
    for prov, n in prov_counts.sort_values(ascending=False).items():
        n_records = len(df[df["provincia_code"] == prov])
        print(f"    Prov {prov:>3s}: {n:>5} municipios, {n_records:>10,} registros")

    # Limpiar columnas temporales
    df.drop(columns=["_valor_num", "_fecha_parsed", "_year_month"], inplace=True, errors="ignore")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("  MERGE DE TODOS LOS CSVs + ANÁLISIS DE CALIDAD DE DATOS")
    print("=" * 70)

    # 1. Merge
    print("\n[1/3] Combinando CSVs...")
    df = merge_all_csvs()

    # 2. Guardar
    print(f"\n[2/3] Guardando CSV consolidado en {OUTPUT_FILE}...")
    df.to_csv(OUTPUT_FILE, index=False)
    size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"  Archivo generado: {size_mb:.1f} MB")

    # 3. Análisis
    print("\n[3/3] Ejecutando análisis de calidad de datos...")
    analyze_data_quality(df)

    print(f"\n{'=' * 70}")
    print("  ANÁLISIS COMPLETADO")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
