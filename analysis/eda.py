import marimo

__generated_with = "0.10.0"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    import pandas as pd
    import numpy as np
    import plotly.express as px
    from pathlib import Path
    from geopy.geocoders import Nominatim
    from geopy.extra.rate_limiter import RateLimiter
    import hashlib
    return Nominatim, Path, RateLimiter, hashlib, mo, np, pd, px


@app.cell
def _(Path):
    _root = Path(__file__).parent.parent
    DATA_DIR = _root / "data" / "raw" / "csvs"
    CACHE_DIR = _root / "data" / "processed"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    GALICIAN_PROVINCES = {"15", "27", "32", "36"}
    PROVINCE_NAMES = {
        "15": "A Coruña",
        "27": "Lugo",
        "32": "Ourense",
        "36": "Pontevedra",
    }
    return CACHE_DIR, DATA_DIR, GALICIAN_PROVINCES, PROVINCE_NAMES


@app.cell
def _(DATA_DIR, GALICIAN_PROVINCES, pd):
    _files = sorted(DATA_DIR.glob("*.csv"))
    _galician = [f for f in _files if f.name.split("_")[0] in GALICIAN_PROVINCES]
    data = pd.concat(
        [pd.read_csv(f, dtype=str) for f in _galician],
        ignore_index=True,
    )
    data.columns = data.columns.str.strip()
    data["fecha_toma"] = pd.to_datetime(
        data["fecha_toma"], format="%d/%m/%Y", errors="coerce"
    )
    n_files = len(_galician)
    return data, n_files


@app.cell
def _(data, n_files, mo, PROVINCE_NAMES):
    _prov = (
        data.groupby("provincia_code")
        .agg(
            municipios=("municipio_code", "nunique"),
            puntos=("punto_muestreo", "nunique"),
            registros=("municipio_code", "size"),
        )
        .reset_index()
    )
    _prov["provincia"] = _prov["provincia_code"].map(PROVINCE_NAMES)

    _rows = "\n".join(
        f"| {r.provincia} | {r.municipios} | {r.puntos} | {r.registros:,} |"
        for r in _prov.itertuples()
    )

    mo.md(f"""
# Calidad del Agua en Galicia

**{len(data):,}** registros de **{n_files}** municipios
&mdash; Fechas: **{data['fecha_toma'].min():%Y-%m-%d}** a **{data['fecha_toma'].max():%Y-%m-%d}**

| Provincia | Municipios | Puntos muestreo | Registros |
|-----------|-----------|-----------------|-----------|
{_rows}
""")
    return


@app.cell
def _(data, pd, Nominatim, RateLimiter, CACHE_DIR, PROVINCE_NAMES, mo):
    """Geocode municipalities using Nominatim. Results are cached to disk."""
    _cache_path = CACHE_DIR / "geocoded_municipalities.csv"

    _munis = (
        data[["provincia_code", "municipio_code", "municipio"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    if _cache_path.exists():
        muni_coords = pd.read_csv(
            _cache_path, dtype={"provincia_code": str, "municipio_code": str}
        )
        _known = set(
            zip(muni_coords["provincia_code"], muni_coords["municipio_code"])
        )
        _todo = _munis[
            ~_munis.apply(
                lambda r: (r["provincia_code"], r["municipio_code"]) in _known,
                axis=1,
            )
        ]
    else:
        muni_coords = pd.DataFrame(
            columns=[
                "provincia_code",
                "municipio_code",
                "municipio",
                "lat_municipio",
                "lon_municipio",
            ]
        )
        _todo = _munis

    if len(_todo) > 0:
        _geo = Nominatim(user_agent="calidad-agua-galicia-eda")
        _lookup = RateLimiter(_geo.geocode, min_delay_seconds=1.1)
        _new_rows = []
        for _, _r in _todo.iterrows():
            _prov_name = PROVINCE_NAMES.get(_r["provincia_code"], "")
            _query = f"{_r['municipio']}, {_prov_name}, Galicia, España"
            try:
                _loc = _lookup(_query)
            except Exception:
                _loc = None
            _new_rows.append(
                {
                    "provincia_code": _r["provincia_code"],
                    "municipio_code": _r["municipio_code"],
                    "municipio": _r["municipio"],
                    "lat_municipio": _loc.latitude if _loc else None,
                    "lon_municipio": _loc.longitude if _loc else None,
                }
            )
        muni_coords = pd.concat(
            [muni_coords, pd.DataFrame(_new_rows)], ignore_index=True
        )
        muni_coords.to_csv(_cache_path, index=False)

    _geocoded = muni_coords["lat_municipio"].notna().sum()
    mo.md(
        f"Municipios geocodificados: **{_geocoded}** / **{len(muni_coords)}** "
        f"(cache: `data/processed/geocoded_municipalities.csv`)"
    )
    return (muni_coords,)


@app.cell
def _(data, muni_coords, pd, np, hashlib):
    """Derive sampling-point coordinates from municipality centroid + deterministic offset."""
    puntos = (
        data[["provincia_code", "municipio_code", "municipio", "punto_muestreo"]]
        .drop_duplicates()
        .merge(
            muni_coords[
                ["provincia_code", "municipio_code", "lat_municipio", "lon_municipio"]
            ],
            on=["provincia_code", "municipio_code"],
            how="left",
        )
    )

    def _hash_offset(name: str, seed: int) -> float:
        """Small deterministic offset (~1 km) based on name hash."""
        h = int(hashlib.md5(f"{name}:{seed}".encode()).hexdigest()[:8], 16)
        return (h / 0xFFFFFFFF - 0.5) * 0.02

    puntos["lat_punto"] = puntos.apply(
        lambda r: r["lat_municipio"] + _hash_offset(str(r["punto_muestreo"]), 0)
        if pd.notna(r["lat_municipio"])
        else np.nan,
        axis=1,
    )
    puntos["lon_punto"] = puntos.apply(
        lambda r: r["lon_municipio"] + _hash_offset(str(r["punto_muestreo"]), 1)
        if pd.notna(r["lon_municipio"])
        else np.nan,
        axis=1,
    )
    return (puntos,)


@app.cell
def _(data, puntos, pd, PROVINCE_NAMES):
    """Aggregate stats per sampling point for the map."""
    # One row per bulletin (each bulletin has multiple parameter rows)
    _bols = data.drop_duplicates(subset=["boletin_id"])

    _stats = (
        _bols.groupby(
            ["provincia_code", "municipio_code", "municipio", "punto_muestreo"]
        )
        .agg(
            n_analisis=("boletin_id", "nunique"),
            fecha_min=("fecha_toma", "min"),
            fecha_max=("fecha_toma", "max"),
            n_apta=(
                "calificacion",
                lambda x: (x == "AGUA APTA PARA EL CONSUMO").sum(),
            ),
            n_total=("calificacion", "size"),
        )
        .reset_index()
    )
    _stats["pct_apta"] = (_stats["n_apta"] / _stats["n_total"] * 100).round(1)
    _stats["provincia"] = _stats["provincia_code"].map(PROVINCE_NAMES)
    _stats["fecha_min"] = _stats["fecha_min"].dt.strftime("%Y-%m-%d")
    _stats["fecha_max"] = _stats["fecha_max"].dt.strftime("%Y-%m-%d")

    map_data = (
        _stats.merge(
            puntos[
                [
                    "provincia_code",
                    "municipio_code",
                    "punto_muestreo",
                    "lat_municipio",
                    "lon_municipio",
                    "lat_punto",
                    "lon_punto",
                ]
            ],
            on=["provincia_code", "municipio_code", "punto_muestreo"],
            how="left",
        )
        .dropna(subset=["lat_punto", "lon_punto"])
    )
    return (map_data,)


@app.cell
def _(mo):
    view_level = mo.ui.radio(
        options={"Punto de muestreo": "punto", "Municipio": "municipio"},
        value="Punto de muestreo",
        label="Nivel de visualizacion",
    )
    view_level
    return (view_level,)


@app.cell
def _(map_data, view_level, px):
    if view_level.value == "municipio":
        _df = (
            map_data.groupby(
                [
                    "provincia",
                    "municipio_code",
                    "municipio",
                    "lat_municipio",
                    "lon_municipio",
                ]
            )
            .agg(
                n_analisis=("n_analisis", "sum"),
                n_apta=("n_apta", "sum"),
                n_total=("n_total", "sum"),
                fecha_min=("fecha_min", "min"),
                fecha_max=("fecha_max", "max"),
                n_puntos=("punto_muestreo", "nunique"),
            )
            .reset_index()
        )
        _df["pct_apta"] = (_df["n_apta"] / _df["n_total"] * 100).round(1)

        _fig = px.scatter_map(
            _df,
            lat="lat_municipio",
            lon="lon_municipio",
            color="pct_apta",
            size="n_analisis",
            hover_name="municipio",
            hover_data={
                "provincia": True,
                "n_analisis": True,
                "n_puntos": True,
                "pct_apta": ":.1f",
                "fecha_min": True,
                "fecha_max": True,
                "lat_municipio": False,
                "lon_municipio": False,
                "municipio_code": False,
                "n_apta": False,
                "n_total": False,
            },
            color_continuous_scale="RdYlGn",
            range_color=[80, 100],
            size_max=18,
            zoom=7,
            center={"lat": 42.7, "lon": -8.0},
            title="Calidad del agua por municipio",
            labels={
                "pct_apta": "% Apta",
                "n_analisis": "Analisis",
                "n_puntos": "Puntos muestreo",
                "provincia": "Provincia",
                "fecha_min": "Desde",
                "fecha_max": "Hasta",
            },
        )
    else:
        _fig = px.scatter_map(
            map_data,
            lat="lat_punto",
            lon="lon_punto",
            color="pct_apta",
            size="n_analisis",
            hover_name="punto_muestreo",
            hover_data={
                "municipio": True,
                "provincia": True,
                "n_analisis": True,
                "pct_apta": ":.1f",
                "fecha_min": True,
                "fecha_max": True,
                "lat_punto": False,
                "lon_punto": False,
                "provincia_code": False,
                "municipio_code": False,
                "n_apta": False,
                "n_total": False,
            },
            color_continuous_scale="RdYlGn",
            range_color=[80, 100],
            size_max=14,
            zoom=7,
            center={"lat": 42.7, "lon": -8.0},
            title="Calidad del agua por punto de muestreo",
            labels={
                "pct_apta": "% Apta",
                "n_analisis": "Analisis",
                "municipio": "Municipio",
                "provincia": "Provincia",
                "fecha_min": "Desde",
                "fecha_max": "Hasta",
            },
        )

    _fig.update_layout(height=700, margin=dict(l=0, r=0, t=40, b=0))
    _fig
    return


if __name__ == "__main__":
    app.run()
