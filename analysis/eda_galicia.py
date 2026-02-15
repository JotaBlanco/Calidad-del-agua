import marimo

__generated_with = "0.19.11"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    import pandas as pd
    from pathlib import Path
    import matplotlib.pyplot as plt
    import seaborn as sns

    sns.set_theme()
    return Path, mo, pd, plt


@app.cell
def _(Path, pd):
    DATA_PATH = Path(__file__).parent.parent / "data" / "processed" / "all_data_galicia.csv"
    df = pd.read_csv(DATA_PATH, dtype=str)
    df["fecha_toma"] = pd.to_datetime(df["fecha_toma"], format="%d/%m/%Y", errors="coerce")
    df["valor"] = df["valor"].astype(float)
    df
    return (df,)


@app.cell
def _(df):
    df["calificacion"].value_counts()
    return


@app.cell
def _(Path, df, pd):
    # Load location catalog for human-readable names
    _catalog = pd.read_csv(
        Path(__file__).parent.parent / "data" / "locations_catalog.csv",
        dtype=str,
    )
    # Build lookup dicts: code -> name
    ccaa_names = dict(zip(_catalog["ccaa_code"], _catalog["ccaa_name"]))
    provincia_names = dict(zip(_catalog["provincia_code"], _catalog["provincia_name"]))
    municipio_names = dict(zip(_catalog["municipio_code"], _catalog["municipio_name"]))

    # Unique codes present in the data
    ccaa_codes_in_data = sorted(df["ccaa_code"].unique())
    return ccaa_codes_in_data, ccaa_names, municipio_names, provincia_names


@app.cell
def _(ccaa_codes_in_data, ccaa_names, mo):
    # CCAA selector
    ccaa_dropdown = mo.ui.dropdown(
        options={ccaa_names.get(c, c): c for c in ccaa_codes_in_data},
        label="Comunidad Autónoma",
    )
    ccaa_dropdown
    return (ccaa_dropdown,)


@app.cell
def _(ccaa_dropdown, df, mo, provincia_names):
    # Provincia selector — filtered by selected CCAA
    _prov_codes = sorted(df.loc[df["ccaa_code"] == ccaa_dropdown.value, "provincia_code"].unique()) if ccaa_dropdown.value else []
    provincia_dropdown = mo.ui.dropdown(
        options={provincia_names.get(p, p): p for p in _prov_codes},
        label="Provincia",
    )
    provincia_dropdown
    return (provincia_dropdown,)


@app.cell
def _(df, mo, municipio_names, provincia_dropdown):
    # Municipio selector — filtered by selected provincia
    _mun_codes = sorted(df.loc[df["provincia_code"] == provincia_dropdown.value, "municipio_code"].unique()) if provincia_dropdown.value else []
    municipio_dropdown = mo.ui.dropdown(
        options={municipio_names.get(m, m): m for m in _mun_codes},
        label="Municipio",
    )
    municipio_dropdown
    return (municipio_dropdown,)


@app.cell
def _(df, mo, municipio_dropdown, provincia_dropdown):
    # Red selector (optional) — filtered by selected municipio
    if provincia_dropdown.value and municipio_dropdown.value:
        _mask = (df["provincia_code"] == provincia_dropdown.value) & (df["municipio_code"] == municipio_dropdown.value)
        _reds = sorted(df.loc[_mask, "red"].unique())
    else:
        _reds = []
    red_dropdown = mo.ui.dropdown(
        options={r: r for r in _reds},
        label="Red (opcional)",
    )
    red_dropdown
    return (red_dropdown,)


@app.cell
def _(df, mo, municipio_dropdown, provincia_dropdown, red_dropdown):
    # Parameter selector — filtered by current location selection
    if provincia_dropdown.value and municipio_dropdown.value:
        _mask = (df["provincia_code"] == provincia_dropdown.value) & (df["municipio_code"] == municipio_dropdown.value)
        if red_dropdown.value:
            _mask = _mask & (df["red"] == red_dropdown.value)
        _params = sorted(df.loc[_mask, "parametro"].unique())
    else:
        _params = []
    parametro_dropdown = mo.ui.dropdown(
        options={p: p for p in _params},
        label="Parámetro",
    )
    parametro_dropdown
    return (parametro_dropdown,)


@app.cell
def _(
    df,
    mo,
    municipio_dropdown,
    parametro_dropdown,
    plot_parameter,
    provincia_dropdown,
    red_dropdown,
):
    # Build filtered df and plot
    if provincia_dropdown.value and municipio_dropdown.value:
        _mask = (
            (df["provincia_code"] == provincia_dropdown.value)
            & (df["municipio_code"] == municipio_dropdown.value)
        )
        if red_dropdown.value:
            _mask = _mask & (df["red"] == red_dropdown.value)

        if parametro_dropdown.value:
            df_filtered = df.loc[_mask]
            plot_parameter(df_filtered, parametro_dropdown.value, disaggregate_by=["red", "punto_muestreo"])
        else:
            mo.md("Selecciona un parámetro para ver el gráfico.")
    else:
        mo.md("Selecciona ubicación y parámetro para ver el gráfico.")
    return (df_filtered,)


@app.cell
def _(df_filtered, parametro_dropdown, plot_parameter):
    plot_parameter(df_filtered, parametro_dropdown.value, disaggregate_by=["red"])
    return


@app.cell
def _(plt):
    def plot_parameter(df, parametro, disaggregate_by=None):
        """Plot a parameter as a time series, optionally disaggregated by columns."""
        mask = df["parametro"] == parametro
        data = df.loc[mask].sort_values("fecha_toma")

        if disaggregate_by is None:
            disaggregate_by = []

        fig, ax = plt.subplots(figsize=(14, 5))

        if not disaggregate_by:
            ax.plot(data["fecha_toma"], data["valor"], marker=".", markersize=3, linewidth=0.8)
        else:
            for keys, group in data.groupby(disaggregate_by):
                label = " | ".join(str(k) for k in (keys if isinstance(keys, tuple) else (keys,)))
                group = group.sort_values("fecha_toma")
                ax.plot(group["fecha_toma"], group["valor"], marker=".", markersize=3, linewidth=0.8, label=label)
            ax.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.5, 1.25), ncol=3)

        ax.set_title(parametro, pad=40)
        ax.set_xlabel("Fecha")
        ax.set_ylabel(data["unidad"].iloc[0] if len(data) else "")
        ax.grid(True, which="both", axis="both", linewidth=0.5, alpha=0.7)
        fig.autofmt_xdate()
        fig.tight_layout()
        return fig

    return (plot_parameter,)


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
