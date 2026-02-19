/**
 * app.js — Main application controller.
 *
 * Orchestrates: MapController, Filters, PuntoList, DetailPanel.
 *
 * Flows:
 *   1. Initial load: fetch index.json + national.json, show all 27K points
 *   2. National marker click: auto-load provincia + select municipio + punto
 *   3. Dropdown selection: cascading filters with tag chips
 *   4. Map marker/cluster click: sync sidebar + detail panel
 *   5. Tag deselect: zoom out to appropriate level
 */

/* global MapController, Filters, PuntoList, DetailPanel */

const App = {
    state: {
        index: null,
        nationalData: null,
        provinciaCache: new Map(),
        historyCache: new Map(),
        currentProvCode: null,
        currentProvData: null,
        currentMuniCode: null,
        currentPuntoNombre: null,
    },

    async init() {
        MapController.init();
        DetailPanel.init();
        PuntoList.init();

        Filters.init({
            onProvincia: (code) => this.onProvinciaChange(code),
            onMunicipio: (code) => this.onMunicipioChange(code),
            onPunto: (nombre) => this.onPuntoChange(nombre),
        });

        PuntoList.showPlaceholder();
        DetailPanel.showPlaceholder();

        // Listen for history load requests from detail panel
        document.addEventListener("load-history", (e) => {
            this._loadAndRefreshHistory(e.detail.rowIdx, e.detail.dictIdx);
        });

        try {
            // Load index and national data in parallel
            const [index, national] = await Promise.all([
                this.fetchJSON("data/index.json"),
                this.fetchJSON("data/national.json"),
            ]);

            this.state.index = index;
            this.state.nationalData = national;

            document.getElementById("last-update").textContent =
                index.generated_at;
            Filters.populateProvincias(index.provincias);
            this.renderStats(index.summary);

            // Show all points on map from the start
            MapController.showNational(national, (pt) =>
                this.onNationalMarkerClick(pt)
            );
        } catch (err) {
            console.error("Failed to load initial data:", err);
        }
    },

    // ── National marker click ─────────────────────────────────────

    async onNationalMarkerClick(pt) {
        // pt = { lat, lon, status, provCode, muniCode, nombre }
        const provCode = pt.provCode;

        // Load provincia if not already loaded
        if (this.state.currentProvCode !== provCode) {
            await this._selectProvincia(provCode);
        }

        const data = this.state.currentProvData;
        if (!data) return;

        // Guard: if user clicked elsewhere during async load, abort
        if (this.state.currentProvCode !== provCode) return;

        // Find and select the municipio
        const muni = data.municipios.find((m) => m.code === pt.muniCode);
        if (muni) {
            this._selectMunicipio(pt.muniCode);

            // Find and select the punto
            const punto = muni.puntos.find((p) => p.nombre === pt.nombre);
            if (punto) {
                this._selectPunto(pt.nombre, punto, muni);
            }
        }
    },

    // ── Dropdown change handlers ──────────────────────────────────

    async onProvinciaChange(code) {
        if (!code) {
            this.state.currentProvCode = null;
            this.state.currentProvData = null;
            this.state.currentMuniCode = null;
            this.state.currentPuntoNombre = null;

            // Return to national view
            MapController.showNational(this.state.nationalData, (pt) =>
                this.onNationalMarkerClick(pt)
            );
            Filters.resetMunicipios();
            PuntoList.showPlaceholder();
            DetailPanel.showPlaceholder();
            Filters.clearTags();
            this.renderStats(this.state.index.summary);
            return;
        }

        await this._selectProvincia(code);
    },

    onMunicipioChange(code) {
        if (!code) {
            this.state.currentMuniCode = null;
            this.state.currentPuntoNombre = null;
            const data = this.state.currentProvData;
            if (!data) return;

            Filters.resetPuntos();
            MapController.showProvincia(
                data,
                (punto, muni) => this.onMarkerClick(punto, muni),
                (markers) => this.onClusterClick(markers)
            );
            PuntoList.showPlaceholder(
                "Selecciona un municipio para ver los puntos."
            );
            DetailPanel.showPlaceholder(
                "Haz clic en un punto del mapa para ver sus mediciones."
            );
            this._updateTags();
            this.renderStats(this._provStats(this.state.currentProvData));
            return;
        }

        this._selectMunicipio(code);
    },

    onPuntoChange(nombre) {
        const data = this.state.currentProvData;
        const muniCode = this.state.currentMuniCode;
        if (!data || !muniCode) return;

        const muni = data.municipios.find((m) => m.code === muniCode);
        if (!muni) return;

        if (!nombre) {
            // Deselect punto — show aggregated view for the municipio
            this.state.currentPuntoNombre = null;
            PuntoList.highlightPunto(null);
            MapController.zoomToMunicipio(muniCode);

            const hist = this._getCachedHistory(this.state.currentProvCode);
            const items = muni.puntos.map((p) => ({ punto: p, muni }));
            if (muni.puntos.length === 1) {
                DetailPanel.showPunto(
                    muni.puntos[0],
                    muni,
                    data.dict,
                    data.dates,
                    hist
                );
            } else {
                DetailPanel.showAggregated(
                    items,
                    data.dict,
                    data.dates,
                    hist
                );
            }
            this._updateTags();
            return;
        }

        const punto = muni.puntos.find((p) => p.nombre === nombre);
        if (punto) {
            this._selectPunto(nombre, punto, muni);
        }
    },

    // ── Map interaction handlers ──────────────────────────────────

    onMarkerClick(punto, muni) {
        const data = this.state.currentProvData;
        if (!data) return;

        // Auto-update municipio if changed
        if (this.state.currentMuniCode !== muni.code) {
            this.state.currentMuniCode = muni.code;
            Filters.populateMunicipios(data.municipios);
            Filters.setMunicipioValue(muni.code);
            Filters.populatePuntos(muni.puntos);

            // Update punto list in sidebar
            const items = muni.puntos.map((p) => ({ punto: p, muni }));
            PuntoList.showPuntos(items, (p, m) => {
                this.onMarkerClick(p, m);
            });
        }

        this.state.currentPuntoNombre = punto.nombre;
        Filters.setPuntoValue(punto.nombre);
        PuntoList.highlightPunto(punto.nombre);

        const hist = this._getCachedHistory(this.state.currentProvCode);
        DetailPanel.showPunto(punto, muni, data.dict, data.dates, hist);
        this._updateTags();
    },

    onClusterClick(markers) {
        const data = this.state.currentProvData;
        if (!data) return;

        const items = markers.map((m) => ({
            punto: m._puntoData,
            muni: m._muniData,
        }));
        const hist = this._getCachedHistory(this.state.currentProvCode);

        // Show puntos in sidebar
        PuntoList.showPuntos(items, (punto, muni) => {
            MapController.highlightPunto(punto.nombre, muni.code);
            this.onMarkerClick(punto, muni);
        });

        // Show aggregated view in detail panel
        DetailPanel.showAggregated(items, data.dict, data.dates, hist);
    },

    // ── Internal selection helpers ────────────────────────────────

    async _selectProvincia(code) {
        try {
            const data = await this.loadProvincia(code);
            this.state.currentProvCode = code;
            this.state.currentProvData = data;
            this.state.currentMuniCode = null;
            this.state.currentPuntoNombre = null;

            Filters.setProvinciaValue(code);
            Filters.populateMunicipios(data.municipios);
            MapController.showProvincia(
                data,
                (punto, muni) => this.onMarkerClick(punto, muni),
                (markers) => this.onClusterClick(markers)
            );
            PuntoList.showPlaceholder(
                "Selecciona un municipio para ver los puntos."
            );
            DetailPanel.showPlaceholder(
                "Haz clic en un punto del mapa para ver sus mediciones."
            );

            // Pre-load history in background
            this._ensureHistory(code);

            this._updateTags();
            this.renderStats(this._provStats(data));
        } catch (err) {
            console.error("Failed to load provincia:", err);
        }
    },

    _selectMunicipio(code) {
        const data = this.state.currentProvData;
        if (!data) return;

        this.state.currentMuniCode = code;
        this.state.currentPuntoNombre = null;
        const muni = data.municipios.find((m) => m.code === code);
        if (!muni) return;

        Filters.setMunicipioValue(code);
        Filters.populatePuntos(muni.puntos);
        MapController.zoomToMunicipio(code);

        // Show punto list in sidebar
        const items = muni.puntos.map((p) => ({ punto: p, muni }));
        PuntoList.showPuntos(items, (punto, m) => {
            this.onMarkerClick(punto, m);
        });

        // Show data in detail panel
        const hist = this._getCachedHistory(this.state.currentProvCode);
        if (muni.puntos.length === 1) {
            DetailPanel.showPunto(
                muni.puntos[0],
                muni,
                data.dict,
                data.dates,
                hist
            );
        } else {
            DetailPanel.showAggregated(items, data.dict, data.dates, hist);
        }

        this._updateTags();
        this.renderStats({
            total: muni.total,
            ok: muni.ok,
            warning: muni.warning,
            danger: muni.danger,
        });
    },

    _selectPunto(nombre, punto, muni) {
        const data = this.state.currentProvData;
        if (!data) return;

        this.state.currentPuntoNombre = nombre;
        Filters.setPuntoValue(nombre);
        PuntoList.highlightPunto(nombre);
        MapController.highlightPunto(nombre, muni.code);

        const hist = this._getCachedHistory(this.state.currentProvCode);
        DetailPanel.showPunto(punto, muni, data.dict, data.dates, hist);
        this._updateTags();
    },

    // ── Selection tags ────────────────────────────────────────────

    _updateTags() {
        const provName = this.state.currentProvCode
            ? this._getProvName(this.state.currentProvCode)
            : null;
        const muniName =
            this.state.currentMuniCode && this.state.currentProvData
                ? this.state.currentProvData.municipios.find(
                      (m) => m.code === this.state.currentMuniCode
                  )?.name
                : null;

        Filters.renderTags(
            provName,
            muniName,
            this.state.currentPuntoNombre,
            (level) => this._onTagDeselect(level)
        );
    },

    _onTagDeselect(level) {
        if (level === "punto") {
            this.state.currentPuntoNombre = null;
            Filters.setPuntoValue("");
            this.onPuntoChange("");
        } else if (level === "municipio") {
            this.state.currentMuniCode = null;
            this.state.currentPuntoNombre = null;
            Filters.setMunicipioValue("");
            this.onMunicipioChange("");
        } else if (level === "provincia") {
            Filters.setProvinciaValue("");
            this.onProvinciaChange("");
        }
    },

    _getProvName(code) {
        if (!this.state.index) return null;
        const prov = this.state.index.provincias.find(
            (p) => p.code === code
        );
        return prov ? prov.name : null;
    },

    // ── History management ──────────────────────────────────────

    async _ensureHistory(provCode) {
        if (this.state.historyCache.has(provCode)) return;
        const promise = this.fetchJSON(
            "data/provincia/" + provCode + "_hist.json"
        ).catch((err) => {
            console.warn("No history for provincia " + provCode, err);
            return { s: {} };
        });
        this.state.historyCache.set(provCode, promise);
        const data = await promise;
        this.state.historyCache.set(provCode, data);
    },

    _getCachedHistory(provCode) {
        if (!provCode) return null;
        const cached = this.state.historyCache.get(provCode);
        if (!cached) return null;
        if (cached instanceof Promise) return null;
        return cached;
    },

    async _loadAndRefreshHistory(rowIdx, dictIdx) {
        const provCode = this.state.currentProvCode;
        if (!provCode) return;
        await this._ensureHistory(provCode);
        const hist = this._getCachedHistory(provCode);
        if (hist) {
            DetailPanel._currentHist = hist;
            DetailPanel.refreshChart(rowIdx, dictIdx);
        }
    },

    // ── Helpers ─────────────────────────────────────────────────

    async loadProvincia(code) {
        if (this.state.provinciaCache.has(code)) {
            return this.state.provinciaCache.get(code);
        }
        const data = await this.fetchJSON(
            "data/provincia/" + code + ".json"
        );
        this.state.provinciaCache.set(code, data);
        return data;
    },

    renderStats(stats) {
        document.getElementById("stat-ok").textContent =
            stats.ok.toLocaleString("es");
        document.getElementById("stat-warning").textContent =
            stats.warning.toLocaleString("es");
        document.getElementById("stat-danger").textContent =
            stats.danger.toLocaleString("es");
        document.getElementById("stat-total").textContent =
            stats.total.toLocaleString("es");
    },

    _provStats(data) {
        return {
            total: data.municipios.reduce((s, m) => s + m.total, 0),
            ok: data.municipios.reduce((s, m) => s + m.ok, 0),
            warning: data.municipios.reduce((s, m) => s + m.warning, 0),
            danger: data.municipios.reduce((s, m) => s + m.danger, 0),
        };
    },

    async fetchJSON(path) {
        const resp = await fetch(path);
        if (!resp.ok) throw new Error("HTTP " + resp.status + " " + path);
        return resp.json();
    },
};

document.addEventListener("DOMContentLoaded", () => App.init());
