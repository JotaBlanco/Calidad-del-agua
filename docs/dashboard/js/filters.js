/**
 * filters.js — Cascading dropdown logic for provincia → municipio → punto.
 *
 * Also manages selection tags (visual chips with X to deselect).
 */

/* global escapeHtml */

const Filters = {
    _provChangeCallback: null,
    _muniChangeCallback: null,
    _puntoChangeCallback: null,

    /**
     * Register callbacks for filter changes.
     * @param {Object} callbacks — { onProvincia, onMunicipio, onPunto }
     */
    init(callbacks) {
        this._provChangeCallback = callbacks.onProvincia;
        this._muniChangeCallback = callbacks.onMunicipio;
        this._puntoChangeCallback = callbacks.onPunto;

        document
            .getElementById("select-provincia")
            .addEventListener("change", (e) => {
                this._provChangeCallback(e.target.value);
            });

        document
            .getElementById("select-municipio")
            .addEventListener("change", (e) => {
                this._muniChangeCallback(e.target.value);
            });

        document
            .getElementById("select-punto")
            .addEventListener("change", (e) => {
                this._puntoChangeCallback(e.target.value);
            });
    },

    // ── Populate dropdowns ─────────────────────────────────────────

    /**
     * Populate the provincia dropdown.
     * @param {Array} provincias — from index.json
     */
    populateProvincias(provincias) {
        const sel = document.getElementById("select-provincia");
        sel.innerHTML = '<option value="">— Todas las provincias —</option>';

        const sorted = [...provincias].sort((a, b) =>
            a.name.localeCompare(b.name, "es")
        );

        for (const p of sorted) {
            const opt = document.createElement("option");
            opt.value = p.code;
            opt.textContent = p.name + " (" + p.total + ")";
            if (p.danger > 0) opt.className = "has-danger";
            else if (p.warning > 0) opt.className = "has-warning";
            sel.appendChild(opt);
        }

        sel.disabled = false;
    },

    /**
     * Populate the municipio dropdown from a provincia's data.
     * @param {Array} municipios — from provincia JSON
     */
    populateMunicipios(municipios) {
        const sel = document.getElementById("select-municipio");
        sel.innerHTML = '<option value="">— Todos los municipios —</option>';

        const sorted = [...municipios].sort((a, b) =>
            a.name.localeCompare(b.name, "es")
        );

        for (const m of sorted) {
            const opt = document.createElement("option");
            opt.value = m.code;
            opt.textContent = m.name + " (" + m.total + ")";
            if (m.danger > 0) opt.className = "has-danger";
            else if (m.warning > 0) opt.className = "has-warning";
            sel.appendChild(opt);
        }

        sel.disabled = false;
        this.resetPuntos();
    },

    /**
     * Populate the punto dropdown from a municipio's data.
     * @param {Array} puntos
     */
    populatePuntos(puntos) {
        const sel = document.getElementById("select-punto");
        sel.innerHTML = '<option value="">— Todos —</option>';

        for (const p of puntos) {
            const opt = document.createElement("option");
            opt.value = p.nombre;
            const indicator =
                p.status === "danger" ? " !!" : p.status === "warning" ? " !" : "";
            opt.textContent = p.nombre + indicator;
            sel.appendChild(opt);
        }

        sel.disabled = false;
    },

    // ── Reset dropdowns ────────────────────────────────────────────

    /** Reset the municipio dropdown to placeholder state. */
    resetMunicipios() {
        const sel = document.getElementById("select-municipio");
        sel.innerHTML = '<option value="">— Selecciona provincia —</option>';
        sel.disabled = true;
        this.resetPuntos();
    },

    /** Reset the punto dropdown to placeholder state. */
    resetPuntos() {
        const sel = document.getElementById("select-punto");
        sel.innerHTML = '<option value="">— Todos —</option>';
        sel.disabled = true;
    },

    // ── Programmatic selection (from map clicks) ───────────────────

    /** Set the provincia dropdown value without triggering its change callback. */
    setProvinciaValue(code) {
        document.getElementById("select-provincia").value = code;
    },

    /** Set the municipio dropdown value without triggering its change callback. */
    setMunicipioValue(code) {
        document.getElementById("select-municipio").value = code;
    },

    /** Set the punto dropdown value without triggering its change callback. */
    setPuntoValue(nombre) {
        document.getElementById("select-punto").value = nombre;
    },

    // ── Selection tags ─────────────────────────────────────────────

    /**
     * Render selection tags showing current filter state.
     * @param {string|null} provName
     * @param {string|null} muniName
     * @param {string|null} puntoName
     * @param {Function} onDeselect — callback(level) where level is "provincia"|"municipio"|"punto"
     */
    renderTags(provName, muniName, puntoName, onDeselect) {
        const container = document.getElementById("selection-tags");
        container.innerHTML = "";

        if (provName) {
            container.appendChild(
                this._createTag("Prov", provName, () => onDeselect("provincia"))
            );
        }
        if (muniName) {
            container.appendChild(
                this._createTag("Muni", muniName, () => onDeselect("municipio"))
            );
        }
        if (puntoName) {
            container.appendChild(
                this._createTag("Punto", puntoName, () => onDeselect("punto"))
            );
        }
    },

    clearTags() {
        document.getElementById("selection-tags").innerHTML = "";
    },

    _createTag(label, text, onRemove) {
        const tag = document.createElement("span");
        tag.className = "selection-tag";
        tag.innerHTML =
            '<span class="tag-label">' +
            escapeHtml(label) +
            "</span>" +
            escapeHtml(text) +
            ' <span class="tag-remove">&times;</span>';
        tag.querySelector(".tag-remove").addEventListener("click", (e) => {
            e.stopPropagation();
            onRemove();
        });
        return tag;
    },
};
