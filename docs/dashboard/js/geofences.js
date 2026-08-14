/**
 * geofences.js — Carga perezosa de los límites administrativos.
 *
 * Sirve las geometrías simplificadas que genera scripts/download_geofences.py:
 *
 *   geo/provincias.geojson       53 features   (~231 KB)
 *   geo/municipios/{NN}.geojson  una por provincia (~200 KB de media)
 *
 * Las provincias se cargan de golpe la primera vez que se necesitan; los
 * municipios se cargan por provincia, así que el navegador nunca descarga los
 * 8.223 polígonos del país (10,3 MB en total).
 *
 * También hay geo/comunidades.geojson descargado en el repo, sin usar
 * todavía: el dashboard no tiene nivel de comunidad autónoma.
 *
 * Ojo con los códigos: el dashboard usa provincia con dos dígitos ("09") pero
 * municipio SIN rellenar ("9001"), mientras que los geofences usan el código
 * INE de 5 dígitos ("09001"). normalizeMunCode() salva esa diferencia.
 */

const Geofences = {
    _provincias: null, // Map<prov_code, Feature>
    _municipios: new Map(), // Map<prov_code, Map<mun_code, Feature>>
    _inflight: new Map(), // deduplica descargas simultáneas

    /** Descarga un JSON, reutilizando la promesa si ya está en vuelo. */
    async _fetch(url) {
        if (this._inflight.has(url)) return this._inflight.get(url);
        const p = fetch(url)
            .then((r) => {
                if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
                return r.json();
            })
            .finally(() => this._inflight.delete(url));
        this._inflight.set(url, p);
        return p;
    },

    _indexBy(featureCollection, key) {
        const map = new Map();
        for (const f of featureCollection.features || []) {
            const code = f.properties && f.properties[key];
            if (code != null) map.set(String(code), f);
        }
        return map;
    },

    /** Códigos INE de municipio: el dashboard los guarda sin ceros a la izquierda. */
    normalizeMunCode(code) {
        return String(code).padStart(5, "0");
    },

    /** Feature de la provincia, o null si no está. */
    async provincia(provCode) {
        if (!this._provincias) {
            const fc = await this._fetch("geo/provincias.geojson");
            this._provincias = this._indexBy(fc, "prov_code");
        }
        return this._provincias.get(String(provCode)) || null;
    },

    /** Feature del municipio, o null si no está. */
    async municipio(provCode, munCode) {
        const prov = String(provCode).padStart(2, "0");
        if (!this._municipios.has(prov)) {
            const fc = await this._fetch(`geo/municipios/${prov}.geojson`);
            this._municipios.set(prov, this._indexBy(fc, "mun_code"));
        }
        return this._municipios.get(prov).get(this.normalizeMunCode(munCode)) || null;
    },
};
