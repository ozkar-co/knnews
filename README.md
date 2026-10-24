# knnews

Filtro personal de noticias. Lee tus fuentes RSS, vectoriza cada articulo y
publica feeds filtrados combinando **dos ejes**:

- **relevancia** (tu gusto): embeddings + KNN sobre ejemplos etiquetados + anclas de tema.
- **importancia** (contexto): saliencia entre fuentes, autoridad y recencia.

La idea es no perder lo importante aunque no sea tu tema, y no inundarte con lo
que no te interesa. El perfil se construye **una sola vez** con un LLM offline
(DeepSeek) y queda congelado: **en runtime no hay LLM ni coste por articulo**.

## Como funciona

```
urls ──ingesta──> articulos ──embed 384d──> clustering por evento (saliencia)
                                              │
                       ┌──────────────────────┴─────────────────────┐
                       ▼                                            ▼
              RELEVANCIA (KNN+anclas)                      IMPORTANCIA (saliencia)
                       └──────────────────────┬─────────────────────┘
                                              ▼
                         dominios (exclusion) + dedup + ranking
                                              ▼
                                   todo + 13 feeds por tema
```

1. **Ingesta**: lee `urls` (formato newsboat) y descarga cada feed.
2. **Vectorizacion**: `embed.ozkr.net` (MiniLM multilingue, 384d).
3. **Clustering**: agrupa el mismo hecho cubierto por varias fuentes (ventana de
   72 h) y calcula la **saliencia** (nº de fuentes distintas).
4. **Relevancia**: `KNN` de vecinos etiquetados + ancla de tema - dislikes.
5. **Importancia**: `0.55*saliencia + 0.25*prior_fuente + 0.20*recencia`.
6. **Dominios**: centroides de `celebrity`, `sports`, `crime` (exclusion dura) y
   `politics` (penalizacion suave), derivados del bootstrap.
7. **Publicacion**: `todo` + 13 temas, con dedup por evento y por titulo, cupo
   por fuente y ranking por `0.6*relevancia + 0.4*importancia`.

## Feeds (los mismos que ya usas)

`todo` + `tema-seguridad`, `tema-linux`, `tema-privacidad`, `tema-ia`,
`tema-programacion`, `tema-cultura-digital`, `tema-ciencia`, `tema-colombia`,
`tema-internacional`, `tema-economia`, `tema-ensayos`, `tema-videojuegos`,
`tema-autosostenibilidad`.

Se publican **siempre los 14** (aunque un tema quede vacio) para no romper las
suscripciones de newsboat. No hay que reconfigurar nada.

## Requisitos

- Python 3.10+ (probado con 3.13).
- Acceso al servicio de embeddings (`KNNEWS_EMBED_URL`, por defecto
  `https://embed.ozkr.net/api/embed`).
- Opcional: systemd o tmux para mantener el servicio en un servidor.
- Solo para re-entrenar: `DEEPSEEK_API_KEY` en `.env` (no se versiona).

## Uso

```bash
./run.sh     # arranca el servicio en :3010 (el ciclo corre cada 2 horas)
```

`run.sh` crea el virtualenv, instala dependencias y arranca. Es lo unico que hay
que ejecutar en el dia a dia.

Comandos internos (mantenimiento, no hacen falta normalmente):

```bash
python -m knnews cycle        # un ciclo completo ahora
python -m knnews classify     # recalcular clasificacion y republicar
python -m knnews bootstrap    # etiquetado offline con LLM (una sola vez)
python -m knnews validate     # comparar bootstrap con etiquetas previas
python -m knnews check-feeds  # auditar si cada fuente trae texto completo
python -m knnews stats
```

## Configuracion: `interests.yaml`

| campo | significado |
| --- | --- |
| `threshold` | relevancia minima para entrar en los feeds. |
| `importance_threshold` | importancia minima (contexto) para entrar. |
| `weight_knn` / `weight_anchor` / `weight_dislike` | pesos del eje de gusto. |
| `weight_salience` / `weight_source_importance` / `weight_recency` | pesos de importancia. |
| `salience_similarity` | coseno para agrupar el mismo hecho (0.72). |
| `cluster_window_hours` | ventana de clustering (72 h). |
| `hard_exclude_domains` | dominios que no se publican nunca (celebrity, sports, crime). |
| `soft_domains` | dominios penalizados (politics). |
| `local_sources` / `local_per_source` | fuentes locales con presencia garantizada en `tema-colombia`. |
| `source_topic` | mapeo fuente -> tema para medios especializados. |
| `topics` | los 13 temas con su descripcion (texto que se vectoriza). |
| `dislikes` | temas que restan score (farandula, deportes, crimen, politica, ofertas). |

## Fuentes: `urls`

Formato newsboat, una por linea, con etiqueta opcional:

```
https://ejemplo.com/feed "~etiqueta"
# las lineas que empiezan por # se ignoran
```

Incluye fuentes locales de Pereira/Eje Cafetero (`latarde`, `eldiario-local`,
`lapatria`, `eje21`) y nacionales (`eltiempo/colombia`). El feed `tema-colombia`
garantiza presencia de las fuentes locales configuradas en `local_sources`.

## Contenido completo

Los feeds emiten `<content:encoded>` (lo que newsboat muestra sin salir del
lector). Para fuentes que solo publican un teaser, `fulltext` descarga la pagina
del articulo y extrae los parrafos del cuerpo. `fulltext` bloquea IPs
privadas/loopback (proteccion SSRF).

## Perfil y entrenamiento

El perfil se construye **una sola vez**:

```bash
python -m knnews bootstrap            # etiqueta el corpus con DeepSeek
python -m knnews validate             # concordancia con etiquetas previas
```

El bootstrap guarda por articulo `llm_relevance`, `llm_importance`, `llm_topic` y
`llm_flags`, deriva los centroides de dominio y los priors por fuente, y deja
`label` para el KNN. Despues el runtime solo usa esos datos: **cero LLM en
produccion**.

## Operacion y datos

- **Lock de ciclo**: evita solapamientos si un ciclo tarda mas de 2 h.
- **Retencion**: a los 60 dias se borra el HTML completo (se conservan resumen,
  vector y etiquetas) y se hace `VACUUM` semanal para controlar el tamaño.
- **DB versionada**: `data/knnews.sqlite3` va en git (lleva el perfil entrenado al
  servidor). Se ignoran los sidecar WAL, `data/feeds/`, `data/manifest.json` y el
  cache de vectores.
- **Despliegue**: en el servidor, como la DB viva cambia, descarta la local y trae
  la versionada:

```bash
git checkout -- data/knnews.sqlite3 && git pull
# reinicia el servicio
```

## Estructura

```
run.sh              punto de entrada (venv + deps + servicio)
urls                fuentes RSS (formato newsboat)
interests.yaml      perfil, importancia y ajustes
requirements.txt    feedparser, numpy, PyYAML
knnews/
  config.py         rutas, constantes y carga de interests.yaml
  store.py          SQLite: articulos, vectores, etiquetas, dominios
  embed.py          cliente del servicio de embeddings
  ingest.py         lectura de feeds, vectorizacion y texto completo
  fulltext.py       extraccion del cuerpo + proteccion SSRF
  cluster.py        clustering por evento y saliencia
  classify.py       relevancia + importancia + dominios
  feeds.py          generacion de los RSS (dedup, cupos, ranking)
  bootstrap.py      etiquetado offline con DeepSeek (una sola vez)
  audit.py          auditoria de fuentes (check-feeds)
  service.py        ciclo completo, lock y planificador
  server.py         servidor HTTP
  cli.py            linea de comandos
data/knnews.sqlite3 perfil entrenado y articulos (VERSIONADO)
data/               feeds, manifest y cache (ignorados)
```
