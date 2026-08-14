/**
 * puntolist.js — Sidebar punto card list.
 *
 * Renders clickable punto cards in the left sidebar,
 * allowing users to browse and select individual sampling points.
 */

/* global escapeHtml, statusLabel */

const PuntoList = {
    container: null,

    init() {
        this.container = document.getElementById("punto-list");
    },

    showPlaceholder(message) {
        this.container.innerHTML =
            '<div class="punto-list-placeholder"><p>' +
            escapeHtml(
                message || "Selecciona un municipio o haz clic en el mapa."
            ) +
            "</p></div>";
    },

    /**
     * Render punto cards from a list of {punto, muni} items.
     * @param {Array} items — [{punto, muni}, ...]
     * @param {Function} onSelect — callback(punto, muni)
     */
    showPuntos(items, onSelect) {
        let html = "";
        for (const { punto, muni } of items) {
            const nTotal = (punto.m || []).length;
            const nFail = (punto.m || []).filter((m) => m[2] > 0).length;
            // punto.nt — distinct dates sampled at this point, over its whole
            // history. Absent in JSONs built before the field existed.
            const nTomas = punto.nt;
            html +=
                '<div class="punto-card" data-punto="' +
                escapeHtml(punto.nombre) +
                '" data-muni="' +
                escapeHtml(muni.code) +
                '">';
            html += '<div class="punto-card-header">';
            html +=
                '<span class="detail-status ' +
                punto.status +
                '" style="font-size:10px;padding:1px 6px">' +
                statusLabel(punto.status) +
                "</span>";
            html +=
                '<span class="punto-card-name">' +
                escapeHtml(punto.nombre) +
                "</span>";
            html += "</div>";
            html +=
                '<div class="punto-card-muni">' +
                escapeHtml(muni.name) +
                "</div>";
            html +=
                '<div class="punto-card-meta">' +
                nTotal +
                " params" +
                (nTomas
                    ? ", " + nTomas + (nTomas === 1 ? " toma" : " tomas")
                    : "") +
                (nFail > 0 ? ", " + nFail + " incumplimiento(s)" : "") +
                "</div>";
            html += "</div>";
        }

        this.container.innerHTML = html;
        this.container.scrollTop = 0;

        this.container.querySelectorAll(".punto-card").forEach((card) => {
            card.addEventListener("click", () => {
                const nombre = card.dataset.punto;
                const muniCode = card.dataset.muni;
                const item = items.find(
                    (i) =>
                        i.punto.nombre === nombre &&
                        i.muni.code === muniCode
                );
                if (item && onSelect) onSelect(item.punto, item.muni);
                // Highlight active card
                this.container
                    .querySelectorAll(".punto-card")
                    .forEach((c) => c.classList.remove("active"));
                card.classList.add("active");
            });
        });
    },

    /** Highlight a specific punto card by name. */
    highlightPunto(nombre) {
        this.container.querySelectorAll(".punto-card").forEach((c) => {
            c.classList.toggle("active", c.dataset.punto === nombre);
        });
    },

    clear() {
        this.showPlaceholder();
    },
};
