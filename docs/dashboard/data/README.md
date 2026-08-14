# Estos ficheros los escribe CI, no tú

Todo lo que hay en esta carpeta (`index.json`, `national.json` y
`provincia/*.json`) lo **genera y commitea** el workflow nocturno
[`update-data.yml`](../../../.github/workflows/update-data.yml), en su job
`publish`. Lo que está en `main` es siempre el build más reciente.

## Por qué importa

Los CSVs de origen (`data/raw/csvs/`) están en `.gitignore`: viven en la caché
de Actions y en la release `raw-data`. Tu copia local de esos CSVs casi siempre
va **por detrás** de la de CI, que añade dos o tres provincias cada noche.

Así que un build local y un `git commit` de esta carpeta republicarían datos más
viejos encima de los más nuevos, sin que nada avise. Por eso
`scripts/build_province_data.py` y `scripts/build_dashboard_data.py` se niegan a
escribir aquí salvo que les pases `--publish` — que es lo que hace CI.

## Cómo se actualiza

| Quiero… | Hago… |
|---|---|
| Que se publique lo scrapeado anoche | Nada. El job `publish` lo hace solo. |
| Republicar las 52 provincias ya | Lanzar `update-data.yml` a mano con `rebuild_all: true` (~20 min). |
| Publicar una provincia concreta | Lanzar `update-data.yml` con `provincia: 15` y `skip_scrape: true`. |
| Trastear en local sin publicar | `python -m scripts.build_province_data 15` — se niega, y te dice cómo forzarlo. |

Si vuelves después de semanas y `git status` muestra ficheros de esta carpeta
modificados, casi seguro son un build local viejo: `git checkout --
docs/dashboard/data/` y a otra cosa. No se pierde nada, se regenera.
