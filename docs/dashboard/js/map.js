/**
 * map.js — Leaflet map controller with greyscale tiles.
 *
 * Supports two modes:
 *   - National view: all ~27K points from national.json (compact arrays)
 *   - Provincia view: detailed points from provincia JSON
 *
 * Cluster colors are based on % of non-compliant points:
 *   >50% → red, >10% → yellow, else green
 */

/* global L */

const MapController = {
    map: null,
    clusterGroup: null,
    markers: [],
    _isNationalView: true,
    _onMarkerClick: null,
    _onClusterClick: null,
    _onNationalMarkerClick: null,

    /** Initialize the Leaflet map centered on Spain with greyscale tiles. */
    init() {
        this.map = L.map("map", { zoomControl: true }).setView([40.0, -3.7], 6);

        // CartoDB Positron — minimal greyscale basemap
        L.tileLayer(
            "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png",
            {
                attribution:
                    '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
                subdomains: "abcd",
                maxZoom: 19,
            }
        ).addTo(this.map);

        // Labels layer on top (so markers sit between base and labels)
        L.tileLayer(
            "https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png",
            {
                subdomains: "abcd",
                maxZoom: 19,
                pane: "overlayPane",
            }
        ).addTo(this.map);

        this._initClusterGroup();
        this.map.addLayer(this.clusterGroup);
    },

    _initClusterGroup() {
        this.clusterGroup = L.markerClusterGroup({
            maxClusterRadius: 40,
            spiderfyOnMaxZoom: true,
            showCoverageOnHover: false,
            zoomToBoundsOnClick: true,
            iconCreateFunction: function (cluster) {
                const children = cluster.getAllChildMarkers();
                let nOk = 0;
                let nWarning = 0;
                let nDanger = 0;
                for (const m of children) {
                    if (m._puntoStatus === "danger") nDanger++;
                    else if (m._puntoStatus === "warning") nWarning++;
                    else nOk++;
                }
                const total = nOk + nWarning + nDanger;
                const nonOkRatio = (nWarning + nDanger) / total;

                let cls;
                if (nonOkRatio > 0.5) cls = "danger";
                else if (nonOkRatio > 0.1) cls = "warning";
                else cls = "ok";

                return L.divIcon({
                    html: "<div><span>" + cluster.getChildCount() + "</span></div>",
                    className: "marker-cluster marker-cluster-" + cls,
                    iconSize: L.point(36, 36),
                });
            },
        });

        // Handle cluster clicks in both views
        this.clusterGroup.on("clusterclick", (e) => {
            const markers = e.layer.getAllChildMarkers();
            if (this._isNationalView) {
                if (this._onNationalClusterClick) {
                    this._onNationalClusterClick(markers);
                }
            } else {
                if (this._onClusterClick) {
                    this._onClusterClick(markers);
                }
            }
        });
    },

    /** Remove all markers from the map. */
    clearMarkers() {
        this.clusterGroup.clearLayers();
        this.markers = [];
    },

    /**
     * Display all national points on the map (initial load).
     * @param {Object} nationalData — { p: [[lat, lon, statusCode, provCode, muniCode, nombre], ...] }
     * @param {Function} onMarkerClick — callback({lat, lon, status, provCode, muniCode, nombre})
     * @param {Function} onClusterClick — callback(markers[])
     */
    showNational(nationalData, onMarkerClick, onClusterClick) {
        this.clearMarkers();
        this._isNationalView = true;
        this._onNationalMarkerClick = onMarkerClick;
        this._onNationalClusterClick = onClusterClick;
        this._onMarkerClick = null;
        this._onClusterClick = null;

        const STATUS_STR = ["ok", "warning", "danger"];

        for (const pt of nationalData.p) {
            const statusStr = STATUS_STR[pt[2]] || "ok";

            const marker = L.circleMarker([pt[0], pt[1]], {
                radius: 5,
                fillColor: statusColor(statusStr),
                color: statusBorder(statusStr),
                weight: 1,
                fillOpacity: 0.8,
            });

            marker._puntoStatus = statusStr;
            marker._nationalData = {
                lat: pt[0],
                lon: pt[1],
                status: statusStr,
                provCode: pt[3],
                muniCode: pt[4],
                nombre: pt[5],
            };

            marker.on("click", () => {
                if (onMarkerClick) onMarkerClick(marker._nationalData);
            });

            this.clusterGroup.addLayer(marker);
            this.markers.push(marker);
        }

        this.map.setView([40.0, -3.7], 6);
    },

    /**
     * Display all puntos from a provincia dataset on the map.
     * @param {Object} provData — parsed provincia JSON
     * @param {Function} onMarkerClick — callback(punto, municipio)
     * @param {Function} onClusterClick — callback(markers[])
     */
    showProvincia(provData, onMarkerClick, onClusterClick) {
        this.clearMarkers();
        this._isNationalView = false;
        this._onMarkerClick = onMarkerClick;
        this._onClusterClick = onClusterClick;
        this._onNationalMarkerClick = null;

        for (const muni of provData.municipios) {
            for (const punto of muni.puntos) {
                if (punto.lat == null || punto.lon == null) continue;

                const marker = L.circleMarker([punto.lat, punto.lon], {
                    radius: 6,
                    fillColor: statusColor(punto.status),
                    color: statusBorder(punto.status),
                    weight: 1.5,
                    fillOpacity: 0.9,
                });

                marker._puntoStatus = punto.status;
                marker._puntoData = punto;
                marker._muniData = muni;

                marker.on("click", () => {
                    if (onMarkerClick) onMarkerClick(punto, muni);
                });

                this.clusterGroup.addLayer(marker);
                this.markers.push(marker);
            }
        }

        if (provData.bounds) {
            this.map.fitBounds(provData.bounds, { padding: [20, 20] });
        }
    },

    /**
     * Zoom to a single municipio's markers.
     * @param {string} muniCode
     */
    zoomToMunicipio(muniCode) {
        const muniMarkers = this.markers.filter(
            (m) => m._muniData && m._muniData.code === muniCode
        );
        if (muniMarkers.length > 0) {
            const group = L.featureGroup(muniMarkers);
            this.map.fitBounds(group.getBounds().pad(0.3));
        }
    },

    /**
     * Highlight and zoom to a specific punto.
     * @param {string} puntoNombre
     * @param {string} muniCode
     */
    highlightPunto(puntoNombre, muniCode) {
        for (const marker of this.markers) {
            if (
                marker._puntoData &&
                marker._puntoData.nombre === puntoNombre &&
                marker._muniData &&
                marker._muniData.code === muniCode
            ) {
                this.map.setView(marker.getLatLng(), 15);
                setTimeout(() => {
                    marker.setStyle({ weight: 3, color: "#222", radius: 9 });
                    setTimeout(() => {
                        marker.setStyle({
                            weight: 1.5,
                            color: statusBorder(marker._puntoStatus),
                            radius: 6,
                        });
                    }, 1500);
                }, 300);
                return;
            }
        }
    },

    /** Reset the map to the default Spain view. */
    resetView() {
        this.clearMarkers();
        this.map.setView([40.0, -3.7], 6);
    },
};

/* ── Helpers ─────────────────────────────────────────────────── */

function statusColor(status) {
    const colors = {
        ok: "#4caf50",
        warning: "#ff9800",
        danger: "#f44336",
    };
    return colors[status] || "#9e9e9e";
}

function statusBorder(status) {
    const colors = {
        ok: "#2e7d32",
        warning: "#e65100",
        danger: "#b71c1c",
    };
    return colors[status] || "#666";
}

function statusLabel(status) {
    const labels = { ok: "Apto", warning: "No apto", danger: "No potable" };
    return labels[status] || "Sin datos";
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}
