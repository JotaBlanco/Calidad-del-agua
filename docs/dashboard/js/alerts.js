/**
 * alerts.js — Warning and danger alert panel.
 *
 * Uses the `m` array on each punto with dict encoding.
 * Measurement tuple: [dict_idx, valor, aptitud_code, date_idx]
 *   aptitud: 0=apta, 1=incumple_vp, 2=no_apta
 */

const AlertsPanel = {
    update(municipios, dict, dates, filterMuni, filterPunto, onItemClick) {
        const dangers = [];
        const warnings = [];

        const munis = filterMuni
            ? municipios.filter((m) => m.code === filterMuni)
            : municipios;

        for (const muni of munis) {
            const puntos = filterPunto
                ? muni.puntos.filter((p) => p.nombre === filterPunto)
                : muni.puntos;

            for (const punto of puntos) {
                const ms = punto.m || [];
                const dangerParams = ms.filter((m) => m[2] === 2);
                const warningParams = ms.filter((m) => m[2] === 1);

                if (dangerParams.length > 0) {
                    dangers.push({
                        municipio: muni.name,
                        muniCode: muni.code,
                        punto: punto.nombre,
                        params: dangerParams,
                    });
                }
                if (warningParams.length > 0) {
                    warnings.push({
                        municipio: muni.name,
                        muniCode: muni.code,
                        punto: punto.nombre,
                        params: warningParams,
                    });
                }
            }
        }

        this._renderList("danger-list", dangers, dict, dates, onItemClick);
        this._renderList("warning-list", warnings, dict, dates, onItemClick);
    },

    clear() {
        document.querySelector("#danger-list ul").innerHTML =
            '<li class="alert-empty">Selecciona una provincia</li>';
        document.querySelector("#warning-list ul").innerHTML =
            '<li class="alert-empty">Selecciona una provincia</li>';
    },

    _renderList(containerId, items, dict, dates, onItemClick) {
        const ul = document.querySelector("#" + containerId + " ul");
        ul.innerHTML = "";

        if (items.length === 0) {
            ul.innerHTML = '<li class="alert-empty">Sin incidencias</li>';
            return;
        }

        for (const item of items) {
            const li = document.createElement("li");
            let html =
                '<div class="alert-item-header">' +
                escapeHtml(item.municipio) +
                " — " +
                escapeHtml(item.punto) +
                "</div>";

            html += '<ul class="alert-param-list">';
            for (const m of item.params) {
                const d = dict[m[0]] || [];
                const name = d[0] || "?";
                const unit = d[1] || "";
                const limite = d[2];
                const limStr =
                    limite == null
                        ? "—"
                        : Array.isArray(limite)
                          ? limite[0] + "–" + limite[1]
                          : limite;
                html +=
                    "<li>" +
                    escapeHtml(name) +
                    ": " +
                    (m[1] != null ? m[1] : "—") +
                    " " +
                    escapeHtml(unit) +
                    " (lím: " +
                    limStr +
                    ")</li>";
            }
            html += "</ul>";

            li.innerHTML = html;
            li.addEventListener("click", () => {
                if (onItemClick) onItemClick(item.punto, item.muniCode);
            });
            ul.appendChild(li);
        }
    },
};
