# knnews

Filtro personal de noticias. Lee tus fuentes RSS, vectoriza cada articulo y
publica **feeds filtrados** segun tu perfil de interes, para no perder tiempo
leyendo titulares que no valen la pena.

En vez de una lista de palabras clave, knnews usa **embeddings multilingues**
(misma representacion semantica para todas las noticias) + un **clasificador KNN**
sobre ejemplos de relevancia ("me interesa / no me interesa"), apoyado en un
pequeno conjunto de **anclas de tema** y una lista de **descartados** descritos en
lenguaje natural.

## Como funciona

```
urls ──feedparser──> articulos ──embed (384d)──> score ──> RSS por tema ──HTTP──> newsboat
                                                    ▲
                          perfil = anclas de tema + ejemplos etiquetados (KNN)
```

1. **Ingesta**: lee `urls` (formato newsboat) y descarga cada feed.
2. **Vectorizacion**: envia titulo + resumen a un servicio de embeddings
   (`embed.ozkr.net`, modelo `paraphrase-multilingual-MiniLM-L12-v2`, 384 dims).
3. **Clasificacion**: `score = weight_knn * KNN + weight_anchor * similitud_al_tema - weight_dislike * similitud_a_descartados`.
   - **KNN**: fraccion ponderada por similitud de vecinos marcados como relevantes.
   - **Ancla**: similitud coseno al tema mas cercano (los temas van en `interests.yaml`).
   - **Descartados**: si el articulo se parece a la lista `dislikes`, se le resta.
4. **Publicacion**: escribe un RSS por tema (`tema-<slug>.xml`) y uno combinado
   (`todo.xml`) con lo que supera el umbral.
5. **Servicio**: un ciclo cada `cycle_hours` horas y un servidor HTTP que sirve
   los feeds para tu cliente (newsboat), tipicamente tras un tunel Cloudflare.

## Requisitos

- Python 3.10+ (probado con 3.13).
- Acceso al servicio de embeddings (`KNNEWS_EMBED_URL`, por defecto
  `https://embed.ozkr.net/api/embed`). Cualquier endpoint compatible que acepte
  `{"texts": [...]}` y devuelva `{"embeddings": [[...]]}` sirve.
- Opcional: systemd o tmux para mantenerlo corriendo en un servidor.

## Uso

```bash
./run.sh              # servicio: servidor en :3010 + ciclo cada 2h
./run.sh cycle        # un ciclo completo ahora (leer, vectorizar, clasificar, publicar)
./run.sh classify     # recalcular la clasificacion y republicar los feeds
./run.sh embed        # vectoriza solo los articulos pendientes
./run.sh stats        # estadisticas de la base
./run.sh check-feeds  # audita si cada fuente trae el texto completo o solo un encabezado
```

`run.sh` crea un virtualenv en `.venv/`, instala las dependencias y ejecuta el
modulo `knnews`. Si no le pasas argumentos, arranca `serve`.

## Configuracion: `interests.yaml`

| campo          | significado                                                        |
| -------------- | ------------------------------------------------------------------ |
| `port`         | puerto HTTP (por defecto 3010).                                    |
| `cycle_hours`  | cada cuantas horas corre el ciclo en modo `serve`.                 |
| `threshold`    | score minimo para entrar en los feeds.                             |
| `topic_floor`  | similitud minima para asignar un tema (si no, cae en `otros`).     |
| `knn_k`        | numero de vecinos etiquetados a considerar.                        |
| `weight_knn` / `weight_anchor` | peso del KNN y de las anclas en el score final.     |
| `weight_dislike` | cuanto resta la similitud con los `dislikes`.                     |
| `dislikes`     | temas que NO te interesan (autos, deportes, farandula…): restan score. |
| `feeds_limit`  | maximo de articulos por feed.                                      |
| `base_url`     | URL publica para los self-links (pon la de tu tunel).             |
| `fulltext`     | intenta descargar el articulo completo de fuentes que solo dan teaser. |
| `fulltext_min_chars` | umbral de contenido por debajo del cual se intenta la pagina. |
| `topics`       | lista de temas: `name`, `slug` y `description` (texto que se vectoriza). |

Cada tema genera un feed `tema-<slug>.xml`. Anade, quita o reescribe temas y
corre `./run.sh classify` para regenerar.

## Fuentes: `urls`

Formato newsboat, una por linea, con etiqueta opcional entre comillas:

```
https://ejemplo.com/feed "~etiqueta"
# las lineas que empiezan por # se ignoran
```

## Salida y newsboat

Con el servicio en marcha:

- `GET /` — indice HTML con todos los feeds.
- `GET /health` — `{"status": "ok"}`.
- `GET /feeds/todo.xml` — combinado.
- `GET /feeds/tema-seguridad.xml` — por tema.

En newsboat suscribe las URLs del tunel, p.ej.:

```
https://noticias.ejemplo.net/feeds/tema-seguridad.xml
https://noticias.ejemplo.net/feeds/tema-linux.xml
```

## Contenido completo

Los feeds se emiten con `<content:encoded>`, que es lo que newsboat muestra al
abrir un articulo (sin salir del lector). La mayoria de las fuentes ya traen el
texto entero en su `<description>`; unas pocas (Ars Technica, LWN, Schneier,
Quanta…) dan un resumen, y otras (The Hacker News, BleepingComputer, phys.org,
Phoronix) solo un encabezado.

Para esas ultimas, con `fulltext: true` knnews descarga la pagina del articulo y
extrae los parrafos del cuerpo, de modo que tambien se lean completas. Usa
`./run.sh check-feeds` para ver el veredicto de cada fuente y decidir si merece
la pena mantenerla.

## Despliegue en un servidor

El servicio escucha en `0.0.0.0:3010`, listo para exponer con un tunel
Cloudflare (apunta el tunel a `http://localhost:3010`). Ejemplo de unidad systemd:

```ini
[Unit]
Description=knnews filtro de noticias
After=network-online.target

[Service]
WorkingDirectory=/opt/knnews
ExecStart=/opt/knnews/run.sh serve
Restart=on-failure
Environment=KNNEWS_PORT=3010

[Install]
WantedBy=multi-user.target
```

## Perfil y entrenamiento

El perfil tiene dos partes y **no hay comandos interactivos**:

- **Etiquetas** (`label` = 1 me interesa / 0 no): se guardan en
  `data/knnews.sqlite3` y se **curan externamente** (el operador del proyecto las
  ajusta); el servicio solo las usa como ejemplos del KNN.
- **Anclas y descartados** (`interests.yaml`): describen tus temas y lo que no te
  interesa (`dislikes`, que restan score). Se pueden editar a mano.

El servicio clasifica en cada ciclo sin intervencion. Para revisar o forzar una
reclasificacion tras cambiar `interests.yaml`: `./run.sh classify`.

La base `data/knnews.sqlite3` **se versiona en git** para que el perfil ya
entrenado viaje al servidor. Se ignoran los sidecar WAL y lo que se regenera
(`data/feeds/`, `data/manifest.json`, `data/profile_vectors.json`).

## Estructura

```
run.sh              punto de entrada (venv + deps + modulo)
urls                fuentes RSS (formato newsboat)
interests.yaml      perfil de interes y ajustes
requirements.txt    feedparser, numpy, PyYAML
knnews/
  config.py         rutas, constantes y carga de interests.yaml
  store.py          SQLite: articulos, vectores, etiquetas
  embed.py          cliente del servicio de embeddings
  ingest.py         lectura de feeds, vectorizacion y texto completo
  fulltext.py       extraccion del cuerpo desde la pagina del articulo
  classify.py       anclas de tema + descartados + KNN
  feeds.py          generacion de los RSS de salida
  audit.py          auditoria de fuentes (check-feeds)
  service.py        ciclo completo y planificador
  server.py         servidor HTTP
  cli.py            linea de comandos
data/knnews.sqlite3 articulos, vectores y etiquetas (VERSIONADO)
data/               feeds, manifest y cache (ignorados)
```
