# Calidad del Agua en España

Pipeline de datos abiertos que extrae, normaliza, geolocaliza y publica los análisis de
calidad del agua de consumo humano de **SINAC** (Sistema de Información Nacional de Agua
de Consumo, Ministerio de Sanidad).

**→ [Dashboard interactivo](https://jotablanco.github.io/Calidad-del-agua/)** ·
**→ [Dataset en Kaggle](https://www.kaggle.com/datasets/javiblanco/calidad-del-agua-espana)**

SINAC publica los boletines de análisis municipio a municipio, en una web sin API y sin
posibilidad de descarga masiva. Este proyecto los recorre todos, los convierte en una tabla
única, evalúa cada medición contra los límites legales del **RD 3/2023** y sitúa cada punto
de muestreo en el mapa.

| | |
|---|---|
| Municipios | 8.131 (100 % del catálogo SINAC) |
| CCAA / provincias | 19 / 52 |
| Puntos de muestreo | ~29.800 |
| Mediciones | ~8,8 M |
| Rango temporal | 2003 → actualidad |
| Parámetros distintos | ~550 (82 con límite legal en el BOE) |
| Laboratorios | ~150 |

## Cómo funciona

```
SINAC (web pública, navegación con estado de sesión)
   │
   ├─ scraper/enumerate_locations.py  → data/locations_catalog.csv   (8.131 municipios)
   │
   ├─ scraper/scrape_all.py           → un CSV por municipio en data/raw/csvs/
   │                                    + progreso por provincia (reanudable)
   │
   ├─ scripts/merge_csvs.py           → data/processed/all_data.csv  (~8,8 M filas)
   │                                    + informe de calidad de datos
   │
   ├─ scripts/run_etl.py  (s0 … s7)   → all_data_enriched.csv + aggregates/agg_*.csv
   │     s0 tipos · s1 normalización · s2 límites expandidos · s3 merge
   │     s4 cumplimiento · s5 último · s6 geo municipio · s7 agregados
   │
   ├─ scripts/geocode_puntos.py       → coordenadas por punto de muestreo
   │                                    (proceso aparte, lento, cacheado)
   │
   └─ scripts/build_dashboard_data.py → docs/dashboard/data/*.json
                                        → GitHub Pages
```

### El scraper

SINAC no tiene API: es una aplicación con estado de sesión en servidor, donde para
descargar un boletín hay que "estar" en la página de su red de distribución. El scraper
reproduce esa navegación y guarda progreso por provincia, de modo que es **reanudable**.

Cada boletín se puede leer por dos vías: el **PDF** (parseado con `pdfplumber`) o la
**página HTML de detalle**. El servicio de PDFs de SINAC se cae con frecuencia, así que el
scraper degrada a HTML por red de distribución y vuelve a intentar el PDF en la siguiente.
Las filas llevan una columna `source` con `pdf` o `html`; al re-raspar, los datos se
**añaden y deduplican** conservando la versión de mayor calidad.

### La evaluación de cumplimiento

Es la parte con más criterio del proyecto. Cada medición se compara con su límite del
anexo I del RD 3/2023 y recibe un veredicto:

| `aptitud` | Significado |
|---|---|
| `apta` | valor ≤ valor paramétrico (VP) |
| `incumple_vp` | VP < valor ≤ valor de no aptitud (VNA) |
| `no_apta` | valor > VNA, o valor > VP en partes sin VNA |

`incumple_vp` **solo existe en la parte C** (indicadores). El art. 6.2 establece que superar
el VP de un indicador obliga a acción correctora pero *no* presume que el agua sea no apta;
esa distinción está codificada en lugar de aplanarse a un simple "cumple/no cumple".

Casos que no son una simple comparación:

- **Rangos**: pH (apta 6,5–9,5; no apta <4,5 o >10,0) e Índice de Langelier (±0,5, sin VNA).

- **Plaguicidas individuales**: el BOE define un límite genérico de 0,10 µg/L que se
  propaga a cada plaguicida concreto que aparece en los datos.
- **Sumatorios**: THM, ácidos haloacéticos, HPA, tricloroeteno+tetracloroeteno y PFAS no
  tienen límite individual. Sus componentes se suman por medición y se comparan contra el
  límite del parámetro suma (`aptitud_suma`).

- **Parámetros sin resultado**: un boletín lista todo lo que cubre, lo haya medido el
  laboratorio o no, así que el 10,4 % de las filas llega con `valor` vacío. Esas filas
  **no se clasifican**. Antes se les aplicaba `fillna(0)`, que las hacía `apta` por ser
  0 ≤ VP (870.465 filas), y en el pH —que se juzga por rango, y donde un valor ausente no
  cae ni dentro ni fuera— salían `incumple_vp` (4.344 filas): 272 puntos aparecían en ámbar
  por un pH que nadie tomó. Un parámetro sin resultado es un problema de frecuencia de
  control, no un veredicto sobre el agua, y así se puede contar como lo primero.

  Caso aparte del mismo problema: las filas cuyo nombre venía partido en `Suma` porque el
  parser leyó el tamaño de la familia («Suma 20 PFAs») como si fuera la concentración. Se
  reparan en `repair_sum_rows()` ([scripts/etl/s0_clean.py](scripts/etl/s0_clean.py)) y el
  parser ya no las produce.

### Qué cuenta como "el estado actual"

Un punto de muestreo no se analiza entero de una vez: hay varios tipos de análisis, con
frecuencias distintas, y cada uno mide un subconjunto de parámetros. Así que «el estado
actual del agua» hay que definirlo, y esa definición vive en un único sitio:
`mediciones_vigentes()` en [scripts/etl/s5_latest.py](scripts/etl/s5_latest.py).

Toma **el último valor de cada parámetro**, aunque proceda de un boletín anterior al más
reciente del punto. La alternativa —quedarse solo con el último boletín— escondería
parámetros que incumplieron y nunca se repitieron, y en la práctica cambia el estado del
3,8 % de los puntos, siempre hacia mejor de lo que corresponde.

Existe un umbral de antigüedad opcional (`MAX_ANTIGUEDAD_DIAS`), **desactivado por
defecto**: que un resultado malo lleve años sin repetirse no lo hace menos relevante, sino
más — señala a la vez un problema de calidad y uno de frecuencia de control, que el RD
3/2023 también regula.

Tanto los agregados como el dashboard pasan por esa función, de modo que sus cifras
coinciden por construcción.

Las dos banderas que la sostienen son `es_ultima_toma_del_tipo` (punto × tipo de análisis)
y `es_ultimo_valor_del_parametro` (punto × parámetro, **sin** tipo de análisis: incluirlo
devolvía el mismo parámetro una vez por tipo de análisis, duplicaba las tablas del
dashboard y dejaba puntos marcados por un incumplimiento ya superado). El dataset publicado
sigue incluyendo sus nombres anteriores, `es_ultimo_analisis` y `es_ultima_medicion`, como
copias en desuso; se eliminarán en una versión futura.

### La geolocalización

Los nombres de punto de muestreo son texto libre escrito por 8.000 ayuntamientos
(`PM_AYTO-COIRO- COIRO/ZA MOAÑA`). [scripts/geocode_puntos.py](scripts/geocode_puntos.py)
los limpia en tres niveles progresivos (crudo → limpieza ligera → limpieza profunda) y
prueba tres proveedores en cada nivel (Photon → Nominatim → CartoCiudad), validando cada
resultado contra el centroide del municipio con distancia de Haversine (<50 km).

Cobertura actual: **81 % de los puntos con coordenada propia**; el resto cae al centroide de
su municipio. Los hallazgos empíricos que sostienen el diseño están comentados en el propio
script — el más contraintuitivo es que **quitar el nombre del municipio de la consulta
empeora los resultados en los tres proveedores**.

## Estructura del repositorio

```
scraper/          extracción desde SINAC
  scrape.py             navegación y parseo (PDF + HTML)
  scrape_all.py         orquestación por provincia, progreso reanudable
  enumerate_locations.py  catálogo de CCAA/provincias/municipios

scripts/
  merge_csvs.py         consolida los CSVs + análisis de calidad de datos
  run_etl.py            orquestador del ETL (--diag para solo diagnóstico)
  etl/s0…s7             los ocho pasos del ETL, uno por fichero
  geocode_puntos.py     geolocalización de puntos de muestreo
  build_dashboard_data.py  genera los JSON del dashboard
  test_scrape_local.sh  ensayo local del scrape nocturno

docs/
  dashboard/            frontend (Leaflet, sin framework ni build)
  *.pdf                 RD 3/2023, Directiva UE 2020/2184, OMS GDWQ 4ª ed.

data/
  locations_catalog.csv catálogo de municipios (en git)
  limits/               límites legales: BOE (en uso), UE y OMS (referencia)
  raw/                  CSVs y PDFs por municipio (fuera de git)
  processed/            datasets consolidados (fuera de git)
```

## Uso

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Extracción** — una provincia, modo HTML, sin guardar PDFs:

```bash
python scraper/scrape_all.py --provincia 36 --skip-pdfs --use-html
python scraper/scrape_all.py --status          # avance global
```

**Pipeline completo** — cuidado, `all_data.csv` ronda los 2,4 GB:

```bash
python scripts/merge_csvs.py
python -m scripts.run_etl                      # --diag para no escribir nada
python -m scripts.build_dashboard_data
```

**Dashboard en local**:

```bash
python docs/dashboard/serve_nocache.py         # http://localhost:8000
```

**Geolocalización** — el recorrido completo tarda horas por los límites de las APIs:

```bash
python scripts/geocode_puntos.py --max-points 50   # prueba
python scripts/geocode_puntos.py                   # todo (usa caché)
```

## Actualización automática

[.github/workflows/update-data.yml](.github/workflows/update-data.yml) refresca **dos
provincias cada noche** a las 02:00 UTC, alternando hasta cubrir las 52. Los CSVs y el
progreso viven en la caché de Actions entre ejecuciones.

Restricciones que condicionan el diseño, por si tocas el workflow:

- Los runners de GitHub **matan cualquier job a las 6 h**, ignorando `timeout-minutes`.
  Los límites están escalonados: **4 h scrape < 5 h 30 job < 6 h runner**.
- **Nunca más de dos scrapers concurrentes**: SINAC se ha caído con paralelismos mayores.
  El tope está en `MAX_PARALLEL`, en el grupo `concurrency` y en el script de pruebas.
- Los cron se desactivan tras 60 días sin *commits* (las ejecuciones no cuentan). Por eso
  el job commitea `data/progress_summary.json` cada noche.
- El ETL pesado es **opt-in** (input `rebuild`): 2,4 GB en pandas no caben en un runner.
  Hoy hay que ejecutarlo en local.

## Limitaciones conocidas

- **El dashboard no se regenera solo.** Depende de correr el ETL en local (ver arriba).
- **19 % de los puntos usan el centroide del municipio** en lugar de coordenada propia.
- **Solo se aplican los límites del BOE.** Las tablas de la UE y la OMS están en
  [data/limits/](data/limits/) pero no conectadas al pipeline.
- La cobertura histórica es muy desigual entre municipios: algunos tienen datos desde 2003
  y otros apenas unos meses.

## Fuentes y licencia

Datos originales: [SINAC](https://sinac.sanidad.gob.es/CiudadanoWeb/), Ministerio de
Sanidad. Marco legal: [RD 3/2023](https://www.boe.es/eli/es/rd/2023/01/10/3/con), que
traspone la [Directiva (UE) 2020/2184](https://eur-lex.europa.eu/eli/dir/2020/2184/oj).

Código y datos derivados bajo **CC-BY-4.0**. Este proyecto no está afiliado al Ministerio
de Sanidad; para usos oficiales, consulta directamente SINAC.
