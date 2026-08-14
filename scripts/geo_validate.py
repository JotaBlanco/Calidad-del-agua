#!/usr/bin/env python3
"""Validación geométrica de coordenadas contra el término municipal.

Sustituye la regla plana de 50 km por una regla escalonada basada en el polígono
del término municipal. Python puro (ray casting): no requiere shapely/geopandas,
porque siempre se conoce a qué municipio pertenece el punto y por tanto sólo hay
que evaluar UN polígono por punto — no hace falta índice espacial.

Polígonos esperados en data/geo/municipios/{PP}.geojson (WGS84), con
propiedades mun_code (5 díg. INE), mun_name, prov_code. Los descarga el script
scripts/download_geofences.py.

Regla escalonada (validate_candidate):
  1. dentro del término                     -> ACEPTAR   ("inside")
  2. fuera pero a <= GRACE_BAND_KM del borde -> ACEPTAR   ("grace")
  3. más lejos                              -> RECHAZAR  ("outside")
  +  a <= CENTROID_COLLAPSE_M del centroide -> RECHAZAR  ("centroid_collapse")
     (el geocodificador devolvió el municipio, no el punto: es un centroide
     disfrazado de hit fino, así que se sigue cascadeando y, si nada mejor
     aparece, se etiqueta honestamente como "centroide")
  +  puntos de infraestructura hídrica       -> EXENTOS del polígono, sólo
     se les aplica el viejo control de distancia ("infra_exempt")
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUNICIPIOS_DIR = ROOT / "data" / "geo" / "municipios"
# Puntos de referencia "a nivel de municipio" (capital del nomenclátor,
# centroide del polígono, núcleo más poblado...). Lo genera
# scripts/audit_centroids.py. Ver _load_reference_points().
REFERENCE_POINTS_CSV = ROOT / "data" / "processed" / "municipal_reference_points.csv"

# --- parámetros de la regla escalonada -------------------------------------
GRACE_BAND_KM = 2.0        # banda de gracia fuera del término (calles frontera,
                           # imprecisión del polígono generalizado)
CENTROID_COLLAPSE_M = 150  # si el candidato cae a menos de esto del centroide
                           # municipal, no es un hit fino: es el centroide
INFRA_MAX_DISTANCE_KM = 50  # control antiguo, sólo para puntos exentos


# ---------------------------------------------------------------------------
# TÉRMINOS DE INFRAESTRUCTURA HÍDRICA — EXENTOS de la regla del polígono
# ---------------------------------------------------------------------------
# Decisión de producto explícita: las captaciones, embalses, pozos, depósitos,
# ETAP, azudes y conducciones están legítimamente FUERA del término municipal al
# que abastecen (un pueblo puede beber de un embalse a 40 km, en otra provincia).
# Aplicarles la regla del polígono los rechazaría en masa y perderíamos
# precisamente los puntos cuya localización real es más informativa.
#
# Por tanto: si el nombre del punto contiene cualquiera de estos términos, se
# OMITE la comprobación de polígono por completo (no se le da un buffer más
# amplio: queda exento) y se mantiene únicamente el viejo control de distancia
# de INFRA_MAX_DISTANCE_KM.
#
# Se comparan sin acentos y sin distinguir mayúsculas, sobre límites de palabra.
INFRA_EXEMPT_TERMS = (
    # --- lista base acordada ---
    "CAPTACION",      # cubre CAPTACIÓN al normalizar acentos
    "EMBALSE",
    "POZO", "POZOS",
    "MANANTIAL",
    "ETAP", "ETAPS",
    "DEPOSITO",       # cubre DEPÓSITO
    "AZUD",
    "PANTANO",
    "SONDEO",
    "GALERIA",        # cubre GALERÍA
    "MINA",
    "PRESA",
    "BALSA",
    "RIO",            # cubre RÍO
    "ARROYO",
    "CANAL",
    "ACEQUIA",
    "CAPTACIONES",    # plural del término obligatorio CAPTACION
    # --- añadidos tras auditar los nombres reales del cache -----------------
    # Frecuencias medidas sobre los 29.822 nombres de
    # data/processed/geocoded_puntos_cache.json con scripts/audit_infra_terms.py.
    # Sólo se añaden términos que (a) aparecen de verdad y (b) designan
    # infraestructura que legítimamente puede estar fuera del término.
    "EDAR",            #  25 apariciones
    "BOMBEO",          #  17
    "BARRANCO",        #  13  (curso de agua, como ARROYO)
    "DEPURADORA",      #   8
    "RIERA",           #   7  (catalán: arroyo)
    "ALJIBE",          #   6  (cisterna, como DEPOSITO)
    "REGATO",          #   4  (gallego: arroyo)
    "POTABILIZADORA",  #   4
    "TORRENTE",        #   1
    "TUBERIA",         #   1
    "ELEVACION",       #   1  (estación de elevación)
)

# --- términos EXPLÍCITAMENTE RECHAZADOS ------------------------------------
# Se consideraron y se descartaron porque, medidos sobre el cache, capturan
# mayoritariamente puntos que NO son infraestructura fuera del término, y
# exentarlos abriría un agujero grande en la validación:
#   TOMA    (182)  captura "TORRE TOMA MUESTRA ..." -> torre de muestreo urbana
#   FONT    (529)  "FONT DE DINS", "FONT DE L'AMOR" -> odónimos catalanes/baleares
#   FONTE   (120)  "FONTE DA TORRE" -> topónimos gallegos urbanos
#   LAGUNA   (48)  "plaza de la laguna", "C/ LAGUNA" -> odónimos
#   PLANTA   (37)  "PLANTA HORTICOLA", "PLANTA PAN RALLADO", "PLANTA BAJA"
# Además, ARQUETA (116), TORRETA (82) y LAVADERO (57) son elementos de red
# situados DENTRO del casco urbano, así que tampoco se exentan.
#
# ADVERTENCIA MEDIDA sobre la lista obligatoria: varios términos exigidos
# aparecen en el cache casi siempre como parte de un ODÓNIMO, no como
# infraestructura — RIO (206: "C/ RÍO GUADIANA", "C/ VIRGEN DEL RIO"),
# CANAL (56: "CARRETERA DEL CANAL"), MINA (16: "PRAZA DA MINA"),
# POZO (60: "C/ POZO DULCE"), ETAP (18: "XARXA ETAP 5 (AV. CATALUNYA)").
# Se mantienen porque la exención es una decisión de producto explícita, pero
# el efecto es que ~5% de los puntos quedan exentos de la regla del polígono y
# una parte de ellos no lo merecería.
INFRA_TERMS_REJECTED = ("TOMA", "FONT", "FONTE", "LAGUNA", "PLANTA",
                        "ARQUETA", "TORRETA", "LAVADERO")


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


_INFRA_RE = re.compile(
    r"\b(" + "|".join(sorted(set(INFRA_EXEMPT_TERMS), key=len, reverse=True)) + r")\b"
)


def is_infra_point(punto: str) -> bool:
    """True si el nombre del punto contiene un término de infraestructura hídrica."""
    if not isinstance(punto, str):
        return False
    return bool(_INFRA_RE.search(_strip_accents(punto).upper()))


def infra_terms_in(punto: str) -> list[str]:
    """Términos de infraestructura encontrados (para auditoría)."""
    if not isinstance(punto, str):
        return []
    return _INFRA_RE.findall(_strip_accents(punto).upper())


# ---------------------------------------------------------------------------
# Geometría (Python puro)
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _rings(geom: dict) -> list:
    """Normaliza Polygon/MultiPolygon a lista de polígonos [exterior, hueco...]."""
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    return geom["coordinates"]


def _in_ring(x: float, y: float, ring) -> bool:
    """Ray casting sobre un anillo."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def in_poly(lon: float, lat: float, polys) -> bool:
    """True si (lon,lat) cae dentro de alguno de los polígonos (respeta huecos)."""
    for poly in polys:
        if not _in_ring(lon, lat, poly[0]):
            continue
        if any(_in_ring(lon, lat, h) for h in poly[1:]):
            continue
        return True
    return False


def dist_to_ring_km(lon: float, lat: float, ring) -> float:
    """Distancia mínima punto->segmentos del anillo, en km (proyección local)."""
    k = 111.32
    kx = k * math.cos(math.radians(lat))
    best = float("inf")
    px, py = lon * kx, lat * k
    for i in range(len(ring) - 1):
        ax, ay = ring[i][0] * kx, ring[i][1] * k
        bx, by = ring[i + 1][0] * kx, ring[i + 1][1] * k
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
        cx, cy = ax + t * dx, ay + t * dy
        d = math.hypot(px - cx, py - cy)
        if d < best:
            best = d
    return best


def dist_to_boundary_km(lon: float, lat: float, polys) -> float:
    """Distancia al borde más cercano del término (sólo anillos exteriores)."""
    return min(dist_to_ring_km(lon, lat, p[0]) for p in polys)


# ---------------------------------------------------------------------------
# Carga de términos municipales
# ---------------------------------------------------------------------------

class MunicipalTerms:
    """Carga perezosa de polígonos municipales por provincia.

    Uso:
        terms = MunicipalTerms()
        t = terms.get("09059")     # código INE de 5 dígitos
        if t: terms.contains("09059", lon, lat)
    """

    def __init__(self, base_dir: Path | None = None,
                 reference_csv: Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else MUNICIPIOS_DIR
        self._provinces: dict[str, dict[str, dict]] = {}
        self._missing_provinces: set[str] = set()
        self._reference_csv = Path(reference_csv) if reference_csv else REFERENCE_POINTS_CSV
        self._refs: dict[str, list[tuple[float, float]]] | None = None

    # -- puntos de referencia a nivel de municipio --------------------------
    def _load_reference_points(self) -> dict[str, list[tuple[float, float]]]:
        """{mun_code: [(lat, lon), ...]} de puntos "a nivel de municipio".

        El filtro de colapso al centroide tiene que comparar contra el punto
        que un geocodificador devuelve cuando NO resuelve la cadena, que es
        el centro del pueblo. Ese punto no siempre coincide con el ancla que
        publicamos: si sólo comparásemos contra el ancla, un municipio cuyo
        ancla es el centroide geométrico del polígono dejaría pasar como "hit
        fino" el centro del pueblo devuelto por Photon. Medido: al cambiar las
        anclas, los colapsos detectados bajaron de 6.111 a 3.819 sólo por eso.
        Por tanto se comparan TODOS los puntos de referencia conocidos.
        """
        if self._refs is not None:
            return self._refs
        refs: dict[str, list[tuple[float, float]]] = {}
        if self._reference_csv.exists():
            import csv as _csv
            with self._reference_csv.open(encoding="utf-8") as fh:
                for row in _csv.DictReader(fh):
                    code = str(row["municipio_code"]).zfill(5)
                    refs.setdefault(code, []).append(
                        (float(row["lat"]), float(row["lon"])))
        self._refs = refs
        return refs

    def reference_points(self, mun_code: str,
                         anchor: tuple[float, float] | None = None
                         ) -> list[tuple[float, float]]:
        """Puntos a nivel de municipio: ancla + fichero de referencias + geo_point_2d."""
        code = str(mun_code).zfill(5)
        pts: list[tuple[float, float]] = []
        if anchor:
            pts.append(anchor)
        pts.extend(self._load_reference_points().get(code, ()))
        t = self.get(code)
        if t and t.get("geo_point"):
            pts.append(t["geo_point"])
        return pts

    # -- carga --------------------------------------------------------------
    def _load_province(self, prov: str) -> dict[str, dict]:
        if prov in self._provinces:
            return self._provinces[prov]
        path = self.base_dir / f"{prov}.geojson"
        if not path.exists():
            self._missing_provinces.add(prov)
            self._provinces[prov] = {}
            return {}
        with path.open(encoding="utf-8") as fh:
            gj = json.load(fh)
        out: dict[str, dict] = {}
        for f in gj.get("features", []):
            props = f.get("properties") or {}
            code = props.get("mun_code")
            geom = f.get("geometry")
            if not code or not geom:
                continue
            polys = _rings(geom)
            xs = [c[0] for p in polys for r in p for c in r]
            ys = [c[1] for p in polys for r in p for c in r]
            if not xs:
                continue
            gp = props.get("geo_point_2d") or {}
            out[str(code)] = {
                "name": props.get("mun_name"),
                "polys": polys,
                "bbox": (min(xs), min(ys), max(xs), max(ys)),
                "geo_point": (gp.get("lat"), gp.get("lon"))
                if isinstance(gp, dict) and gp.get("lat") is not None else None,
            }
        self._provinces[prov] = out
        return out

    def get(self, mun_code: str) -> dict | None:
        mun_code = str(mun_code).zfill(5)
        return self._load_province(mun_code[:2]).get(mun_code)

    def province(self, prov_code: str) -> dict[str, dict]:
        return self._load_province(str(prov_code).zfill(2))

    @property
    def missing_provinces(self) -> set[str]:
        return set(self._missing_provinces)

    # -- consultas ----------------------------------------------------------
    def contains(self, mun_code: str, lon: float, lat: float) -> bool | None:
        """True/False, o None si no hay polígono para ese municipio."""
        t = self.get(mun_code)
        if not t:
            return None
        x0, y0, x1, y1 = t["bbox"]
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            return False
        return in_poly(lon, lat, t["polys"])

    def distance_outside_km(self, mun_code: str, lon: float, lat: float) -> float | None:
        """Distancia al borde del término (0.0 si está dentro), None si no hay polígono."""
        t = self.get(mun_code)
        if not t:
            return None
        if self.contains(mun_code, lon, lat):
            return 0.0
        return dist_to_boundary_km(lon, lat, t["polys"])


# ---------------------------------------------------------------------------
# La regla escalonada
# ---------------------------------------------------------------------------

VERDICTS_ACCEPT = {"inside", "grace", "infra_exempt", "no_polygon"}


def validate_candidate(
    lat: float,
    lon: float,
    mun_code: str,
    centroid: tuple[float, float],
    punto: str,
    terms: MunicipalTerms,
) -> tuple[bool, str, float]:
    """Valida una coordenada candidata contra el término municipal.

    Devuelve (aceptar, veredicto, distancia_km).
    Veredictos:
      inside            dentro del término                      -> aceptar
      grace             fuera, <= GRACE_BAND_KM del borde        -> aceptar
      infra_exempt      punto de infraestructura hídrica, exento -> aceptar
      no_polygon        no hay polígono; se usa el control viejo -> aceptar
      centroid_collapse cae sobre el centroide municipal         -> rechazar
      outside           fuera del término y de la banda          -> rechazar
      too_far           exento/sin polígono pero > 50 km         -> rechazar
    """
    # (a) colapso al centroide: se comprueba SIEMPRE, incluso para infra,
    #     porque un candidato en el centroide nunca es un hit fino real.
    #     Se compara contra TODOS los puntos de referencia a nivel de
    #     municipio, no sólo contra el ancla publicada (ver
    #     MunicipalTerms._load_reference_points).
    d_centroid = haversine_km(centroid[0], centroid[1], lat, lon)
    d_ref = min(
        (haversine_km(p[0], p[1], lat, lon)
         for p in terms.reference_points(mun_code, centroid)),
        default=d_centroid,
    )
    if d_ref * 1000.0 <= CENTROID_COLLAPSE_M:
        return (False, "centroid_collapse", d_ref)

    # (b) exención de infraestructura hídrica: sin regla de polígono
    if is_infra_point(punto):
        if d_centroid <= INFRA_MAX_DISTANCE_KM:
            return (True, "infra_exempt", d_centroid)
        return (False, "too_far", d_centroid)

    # (c) regla del polígono
    t = terms.get(mun_code)
    if not t:
        # sin polígono: degradar al control antiguo de distancia
        if d_centroid <= INFRA_MAX_DISTANCE_KM:
            return (True, "no_polygon", d_centroid)
        return (False, "too_far", d_centroid)

    if terms.contains(mun_code, lon, lat):
        return (True, "inside", 0.0)

    d_border = dist_to_boundary_km(lon, lat, t["polys"])
    if d_border <= GRACE_BAND_KM:
        return (True, "grace", d_border)
    return (False, "outside", d_border)
