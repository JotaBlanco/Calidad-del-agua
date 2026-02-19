/**
 * detail.js — Right-side detail panel: full parameter table with expandable charts.
 *
 * Supports two modes:
 *   - Single punto: all parameters for one punto (showPunto)
 *   - Aggregated: latest value per parameter across multiple puntos (showAggregated)
 *
 * Dict entry: [name, unit, limite, vna, parte]
 *              0     1     2       3    4
 *   limite/vna: number | [lo, hi] | null
 *
 * Measurement tuple: [dict_idx, valor, aptitud_code, date_idx]
 *                     0         1      2              3
 *   aptitud: 0=apta, 1=incumple_vp, 2=no_apta
 */

/* global escapeHtml, statusLabel, Chart */

const APTITUD_LABELS = { 0: "Apta", 1: "No apto", 2: "No potable" };
const APTITUD_CLASSES = { 0: "apta", 1: "incumple_vp", 2: "no_apta" };

const DetailPanel = {
    container: null,
    _currentDict: null,
    _currentDates: null,
    _currentHistKey: null,
    _currentHist: null,
    _currentItems: null, // [{punto, muni}] for aggregated mode
    _expandedRow: null,

    init() {
        this.container = document.getElementById("detail-content");
    },

    showPlaceholder(message) {
        this._expandedRow = null;
        this._currentItems = null;
        this.container.innerHTML =
            '<div class="detail-placeholder"><p>' +
            escapeHtml(
                message ||
                    "Haz clic en un punto del mapa para ver sus mediciones."
            ) +
            "</p></div>";
    },

    /**
     * Show the full measurement table for a single punto.
     */
    showPunto(punto, muni, dict, dates, hist) {
        this._currentDict = dict;
        this._currentDates = dates;
        this._currentHist = hist;
        this._currentHistKey = muni.code + "|" + punto.nombre;
        this._currentItems = null;
        this._expandedRow = null;

        const measurements = punto.m || [];
        const nTotal = measurements.length;
        const nApta = measurements.filter((m) => m[2] === 0).length;

        let html = "";

        // Header
        html += '<div class="detail-header">';
        html += "<h2>" + escapeHtml(punto.nombre) + "</h2>";
        html +=
            '<div class="detail-sub">' + escapeHtml(muni.name) + "</div>";
        html +=
            '<span class="detail-status ' +
            punto.status +
            '">' +
            statusLabel(punto.status) +
            "</span>";
        html +=
            '<span class="detail-counts">' +
            nApta +
            "/" +
            nTotal +
            " dentro de los limites</span>";
        html += "</div>";

        // Table
        html += this._buildTable(measurements, dict, dates, 6);

        this.container.innerHTML = html;
        this.container.scrollTop = 0;
        this._attachRowHandlers();
    },

    /**
     * Show aggregated view: latest value per parameter across multiple puntos.
     * @param {Array} items — [{punto, muni}, ...]
     * @param {Array} dict
     * @param {Array} dates
     * @param {Object|null} hist
     */
    showAggregated(items, dict, dates, hist) {
        this._currentDict = dict;
        this._currentDates = dates;
        this._currentHist = hist;
        this._currentHistKey = null;
        this._currentItems = items;
        this._expandedRow = null;

        // Aggregate: for each dict_idx, keep the measurement with the latest date
        const aggregated = new Map();

        for (const { punto, muni } of items) {
            for (const m of punto.m || []) {
                const dictIdx = m[0];
                const valor = m[1];
                const aptCode = m[2];
                const dateIdx = m[3];
                const dateStr =
                    dateIdx >= 0 && dates[dateIdx] ? dates[dateIdx] : null;

                const existing = aggregated.get(dictIdx);
                if (
                    !existing ||
                    (dateStr &&
                        (!existing.dateStr || dateStr > existing.dateStr))
                ) {
                    aggregated.set(dictIdx, {
                        dictIdx,
                        valor,
                        aptCode,
                        dateIdx,
                        dateStr,
                        puntoNombre: punto.nombre,
                    });
                }
            }
        }

        // Sort: danger first, then warning, then ok
        const rows = [...aggregated.values()].sort((a, b) => {
            if (a.aptCode !== b.aptCode) return b.aptCode - a.aptCode;
            return a.dictIdx - b.dictIdx;
        });

        // Header
        const nDanger = rows.filter((r) => r.aptCode === 2).length;
        const nWarning = rows.filter((r) => r.aptCode === 1).length;
        let html = '<div class="detail-header">';
        html +=
            "<h2>" + items.length + " puntos seleccionados</h2>";
        html += '<span class="detail-counts">' + rows.length + " params";
        if (nDanger) html += ", " + nDanger + " no potable";
        if (nWarning) html += ", " + nWarning + " no apto";
        html += "</span>";
        html += "</div>";

        // Table with extra Punto column
        html += '<div class="table-scroll">';
        html += '<table class="params-table">';
        html += "<thead><tr>";
        html += "<th>Fecha</th>";
        html += "<th>Param.</th>";
        html += "<th>Uds</th>";
        html += '<th class="num-col">Valor</th>';
        html += '<th class="num-col">Limite</th>';
        html += "<th>Aptitud</th>";
        html += "<th>Punto</th>";
        html += "</tr></thead>";
        html += "<tbody>";

        for (let i = 0; i < rows.length; i++) {
            const r = rows[i];
            const d = dict[r.dictIdx] || [];
            const paramName = d[0] || "";
            const unit = d[1] || "";
            const limite = d[2];
            const aptClass = APTITUD_CLASSES[r.aptCode] || "";
            const aptLabel = APTITUD_LABELS[r.aptCode] || "—";

            const rowClass =
                r.aptCode === 2
                    ? "row-danger"
                    : r.aptCode === 1
                      ? "row-warning"
                      : "";

            html +=
                '<tr class="param-row ' +
                rowClass +
                '" data-idx="' +
                i +
                '" data-dict="' +
                r.dictIdx +
                '">';
            html += "<td>" + this._formatDate(r.dateStr || "—") + "</td>";
            html += "<td>" + escapeHtml(paramName) + "</td>";
            html += "<td>" + escapeHtml(unit) + "</td>";
            html +=
                '<td class="num-col">' +
                (r.valor != null ? r.valor : "—") +
                "</td>";
            html +=
                '<td class="num-col limite-cell">' +
                this._formatLimite(limite) +
                "</td>";
            html +=
                '<td class="aptitud-cell aptitud-' +
                aptClass +
                '">' +
                aptLabel +
                "</td>";
            html +=
                "<td>" + escapeHtml(r.puntoNombre) + "</td>";
            html += "</tr>";
            // Chart placeholder
            html +=
                '<tr class="chart-row" id="chart-row-' +
                i +
                '" style="display:none"><td colspan="7"><div class="chart-container"><canvas id="chart-canvas-' +
                i +
                '"></canvas><div class="chart-loading" id="chart-loading-' +
                i +
                '">Cargando historico...</div></div></td></tr>';
        }

        html += "</tbody></table>";
        html += "</div>";

        this.container.innerHTML = html;
        this.container.scrollTop = 0;

        this._aggregatedRows = rows;
        this._attachRowHandlers();
    },

    // ── Shared table building (single punto) ──────────────────────

    _buildTable(measurements, dict, dates, colspan) {
        let html = '<div class="table-scroll">';
        html += '<table class="params-table">';
        html += "<thead><tr>";
        html += "<th>Fecha</th>";
        html += "<th>Param.</th>";
        html += "<th>Uds</th>";
        html += '<th class="num-col">Valor</th>';
        html += '<th class="num-col">Limite</th>';
        html += "<th>Aptitud</th>";
        html += "</tr></thead>";
        html += "<tbody>";

        for (let i = 0; i < measurements.length; i++) {
            const m = measurements[i];
            const dictIdx = m[0];
            const valor = m[1];
            const aptCode = m[2];
            const dateIdx = m[3];

            const d = dict[dictIdx] || [];
            const paramName = d[0] || "";
            const unit = d[1] || "";
            const limite = d[2];

            const dateStr =
                dateIdx >= 0 && dates[dateIdx] ? dates[dateIdx] : "—";
            const limiteDisplay = this._formatLimite(limite);
            const aptClass = APTITUD_CLASSES[aptCode] || "";
            const aptLabel = APTITUD_LABELS[aptCode] || "—";

            const rowClass =
                aptCode === 2
                    ? "row-danger"
                    : aptCode === 1
                      ? "row-warning"
                      : "";

            html +=
                '<tr class="param-row ' +
                rowClass +
                '" data-idx="' +
                i +
                '" data-dict="' +
                dictIdx +
                '">';
            html += "<td>" + this._formatDate(dateStr) + "</td>";
            html += "<td>" + escapeHtml(paramName) + "</td>";
            html += "<td>" + escapeHtml(unit) + "</td>";
            html +=
                '<td class="num-col">' +
                (valor != null ? valor : "—") +
                "</td>";
            html +=
                '<td class="num-col limite-cell">' + limiteDisplay + "</td>";
            html +=
                '<td class="aptitud-cell aptitud-' +
                aptClass +
                '">' +
                aptLabel +
                "</td>";
            html += "</tr>";
            html +=
                '<tr class="chart-row" id="chart-row-' +
                i +
                '" style="display:none"><td colspan="' +
                colspan +
                '"><div class="chart-container"><canvas id="chart-canvas-' +
                i +
                '"></canvas><div class="chart-loading" id="chart-loading-' +
                i +
                '">Cargando historico...</div></div></td></tr>';
        }

        html += "</tbody></table>";
        html += "</div>";
        return html;
    },

    _attachRowHandlers() {
        this.container.querySelectorAll(".param-row").forEach((row) => {
            row.addEventListener("click", () => {
                const idx = parseInt(row.dataset.idx);
                const dictIdx = parseInt(row.dataset.dict);
                this._toggleChart(idx, dictIdx);
            });
        });
    },

    /** Toggle the chart expansion for a parameter row. */
    _toggleChart(rowIdx, dictIdx) {
        const chartRow = document.getElementById("chart-row-" + rowIdx);
        if (!chartRow) return;

        // Collapse previously expanded row
        if (this._expandedRow !== null && this._expandedRow !== rowIdx) {
            const prev = document.getElementById(
                "chart-row-" + this._expandedRow
            );
            if (prev) prev.style.display = "none";
        }

        if (chartRow.style.display === "none") {
            chartRow.style.display = "table-row";
            this._expandedRow = rowIdx;
            this._renderChart(rowIdx, dictIdx);
        } else {
            chartRow.style.display = "none";
            this._expandedRow = null;
        }
    },

    /** Render the chart for a parameter, loading history if needed. */
    _renderChart(rowIdx, dictIdx) {
        const canvas = document.getElementById("chart-canvas-" + rowIdx);
        const loading = document.getElementById("chart-loading-" + rowIdx);
        if (!canvas) return;

        const d = this._currentDict[dictIdx] || [];
        const limits = { vp: d[2], vna: d[3] };
        const hist = this._currentHist;
        const pidx = String(dictIdx);

        if (this._currentItems && this._currentItems.length > 1) {
            // Aggregated mode: merge series from all puntos
            let combined = [];

            if (hist && hist.s) {
                for (const { punto, muni } of this._currentItems) {
                    const key = muni.code + "|" + punto.nombre;
                    if (hist.s[key] && hist.s[key][pidx]) {
                        combined = combined.concat(hist.s[key][pidx]);
                    }
                }
            }

            if (combined.length > 0) {
                combined.sort((a, b) => (a[0] > b[0] ? 1 : -1));
                if (loading) loading.style.display = "none";
                canvas.style.display = "block";
                Chart.render(canvas, combined, limits);
            } else if (hist === null) {
                if (loading) {
                    loading.style.display = "block";
                    loading.textContent = "Cargando historico...";
                }
                canvas.style.display = "none";
                document.dispatchEvent(
                    new CustomEvent("load-history", {
                        detail: { rowIdx, dictIdx },
                    })
                );
            } else {
                if (loading) {
                    loading.style.display = "block";
                    loading.textContent = "Sin datos historicos";
                }
                canvas.style.display = "none";
            }
        } else {
            // Single punto mode
            const histKey = this._currentHistKey;

            if (hist && hist.s && hist.s[histKey] && hist.s[histKey][pidx]) {
                if (loading) loading.style.display = "none";
                canvas.style.display = "block";
                Chart.render(canvas, hist.s[histKey][pidx], limits);
            } else if (hist === null) {
                if (loading) {
                    loading.style.display = "block";
                    loading.textContent = "Cargando historico...";
                }
                canvas.style.display = "none";
                document.dispatchEvent(
                    new CustomEvent("load-history", {
                        detail: { rowIdx, dictIdx },
                    })
                );
            } else {
                if (loading) {
                    loading.style.display = "block";
                    loading.textContent =
                        "Sin datos historicos (medicion unica)";
                }
                canvas.style.display = "none";
            }
        }
    },

    /** Re-render a chart after history data is loaded. */
    refreshChart(rowIdx, dictIdx) {
        this._renderChart(rowIdx, dictIdx);
    },

    _formatLimite(limite) {
        if (limite == null) return "—";
        if (Array.isArray(limite)) {
            return limite[0] + " – " + limite[1];
        }
        return String(limite);
    },

    _formatDate(dateStr) {
        if (!dateStr || dateStr === "—") return "—";
        // "2026-01-15" → "15/01/2026"
        const parts = dateStr.split("-");
        if (parts.length === 3)
            return parts[2] + "/" + parts[1] + "/" + parts[0];
        return dateStr;
    },
};
