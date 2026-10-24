"""Bootstrap offline: etiqueta el corpus con un LLM (una sola vez).

No se usa en runtime. Genera:
  - llm_relevance / llm_importance / llm_topic / llm_flags por articulo
  - label para el KNN (relevance >= 2)
  - centroides de dominio (celebrity, sports, crime, politics)
  - priors de importancia por fuente
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from . import config, store

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
MAX_SUMMARY = 1200
FLAG_NAMES = ["celebrity", "sports", "crime", "politics", "hacking", "ai", "local_pereira"]

SYSTEM_PROMPT = """Eres un curador de noticias para una persona con este perfil exacto:

LE INTERESA:
- Tecnologia, ultimos avances, IA, computacion, ciencia, cultura geek.
- Libertad, censura, privacidad, vigilancia, tecnologias disruptivas.
- Vivir fuera de la red, autosostenibilidad, neoludismo y critica de la tecnologia.
- Ensayos, opinion, filosofia, logica, ciencia general: articulos que hagan pensar.
- Contexto del mundo: hechos sistemicos importantes, aunque no sean de su gusto.
- Noticias locales de Pereira, Risaralda y el Eje Cafetero (Manizales, Armenia,
  Quindio, Caldas): lo que pase en su region, aunque no sea tecnologia.
- Videojuegos y cine: solo lo mas relevante, con moderacion.

NO LE INTERESA:
- Politica partidista y campanas electorales.
- Farandula, celebridades, deportes.
- Crimen comun, narcotrafico, operativos policiales (le importa, pero prefiere
  ignorarlo, salvo que sea un hecho local muy relevante de Pereira/Eje Cafetero).

Evalua el articulo y devuelve SOLO un objeto JSON valido, sin markdown:
{
  "relevance": 0,
  "importance": 0,
  "topic": "otros",
  "flags": {"news": false, "opinion": false, "essay": false, "science": false,
            "tech": false, "geek": false, "politics": false, "celebrity": false,
            "sports": false, "crime": false, "hacking": false, "ai": false,
            "local_pereira": false},
  "entities": [],
  "reason": ""
}

Reglas:
- relevance (0-3): afinidad con sus gustos. 3 = muy alineado; 2 = si; 1 = tangencial; 0 = nada.
- importance (0-5): importancia global/contexto, INDEPENDIENTE de su gusto. 5 = hecho mundial decisivo; 3 = relevante en su campo; 1 = menor; 0 = irrelevante.
- topic: uno de [seguridad, linux, privacidad, ia, programacion, cultura-digital, ciencia, colombia, internacional, economia, ensayos, videojuegos, autosostenibilidad, otros].
- flags: marca todos los que apliquen. local_pereira = Colombia y especificamente Pereira/Risaralda/Eje Cafetero.
- No mezcles relevance con importance. Un hecho importante de farandula puede tener importance alto y relevance 0.
- reason: maximo 15 palabras.
"""


def _load_env():
    env_path = config.BASE_DIR / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _parse_json(content):
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("sin JSON")
    return json.loads(text[start : end + 1])


def _call(api_key, article):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"FUENTE: {article['source']}\n"
                    f"FECHA: {article['published'] or ''}\n"
                    f"TITULO: {article['title']}\n"
                    f"RESUMEN: {(article['summary'] or article['text'] or '')[:MAX_SUMMARY]}"
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    last = None
    for attempt in range(5):
        req = urllib.request.Request(
            API_URL,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return _parse_json(body["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fallo tras reintentos: {last}")


def _pending(con, limit, force):
    where = "vector IS NOT NULL"
    if not force:
        where += " AND bootstrap_at IS NULL"
    sql = (
        "SELECT id, source, title, summary, text, published FROM articles "
        f"WHERE {where} ORDER BY id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in con.execute(sql)]


def _normalize(vector):
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def derive(con, log=print):
    rows = con.execute(
        "SELECT vector, llm_flags, llm_relevance, llm_importance, source "
        "FROM articles WHERE llm_flags IS NOT NULL AND vector IS NOT NULL"
    ).fetchall()
    parsed = []
    for row in rows:
        try:
            flags = json.loads(row["llm_flags"])
        except (ValueError, TypeError):
            continue
        parsed.append((store.blob_to_vector(row["vector"]), flags))

    for flag in FLAG_NAMES:
        vectors = [vec for vec, flags in parsed if flags.get(flag)]
        if len(vectors) < 15:
            continue
        centroid = _normalize(np.mean(np.vstack(vectors).astype(np.float32), axis=0))
        store.upsert_domain(con, flag, centroid, len(vectors))
        log(f"  dominio {flag}: {len(vectors)} ejemplos")

    for row in con.execute(
        """SELECT source, COUNT(*) n, AVG(llm_relevance) r, AVG(llm_importance) i
           FROM articles WHERE llm_importance IS NOT NULL GROUP BY source"""
    ):
        store.upsert_source_stat(con, row["source"], row["n"], row["r"], row["i"])
    store.set_meta(con, "bootstrap_at", store.now())
    store.set_meta(con, "bootstrap_model", MODEL)
    store.set_meta(con, "bootstrap_articles", len(parsed))
    con.commit()
    log(f"perfil derivado de {len(parsed)} articulos etiquetados")


def run(limit=None, workers=8, dry_run=False, force=False, derive_only=False, log=print):
    _load_env()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    con = store.connect()
    store.init(con)

    if derive_only:
        derive(con, log=log)
        con.close()
        return

    if not api_key:
        raise SystemExit("Falta DEEPSEEK_API_KEY (en .env o entorno)")

    pending = _pending(con, limit, force)
    if not pending:
        log("no hay articulos pendientes de bootstrap")
        derive(con, log=log)
        con.close()
        return

    log(f"bootstrap: {len(pending)} articulos, {workers} workers"
        + (" (dry-run)" if dry_run else ""))

    if not dry_run:
        snapshot = {
            str(row["id"]): row["label"]
            for row in con.execute(
                "SELECT id, label FROM articles WHERE label IS NOT NULL"
            )
        }
        path = config.DATA_DIR / "labels_before_bootstrap.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")

    done = 0
    errors = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_call, api_key, art): art for art in pending}
        for future in as_completed(futures):
            art = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                errors += 1
                log(f"  ! {art['id']} {art['title'][:40]}: {exc}")
                continue
            relevance = int(result.get("relevance", 0))
            importance = int(result.get("importance", 0))
            topic = str(result.get("topic") or "otros")
            flags = result.get("flags") or {}
            reason = str(result.get("reason") or "")
            if dry_run:
                log(
                    f"  [{relevance}/{importance}] {topic:>15} "
                    f"{art['title'][:60]} :: {reason}"
                )
            else:
                con.execute(
                    """UPDATE articles SET llm_relevance=?, llm_importance=?,
                       llm_topic=?, llm_flags=?, label=?, labeled_at=?,
                       bootstrap_at=? WHERE id=?""",
                    (
                        relevance,
                        importance,
                        topic,
                        json.dumps(flags, ensure_ascii=False),
                        1 if relevance >= 2 else 0,
                        store.now(),
                        store.now(),
                        art["id"],
                    ),
                )
            done += 1
            if done % 50 == 0:
                if not dry_run:
                    con.commit()
                elapsed = time.time() - started
                rate = done / elapsed if elapsed else 0
                log(f"  {done}/{len(pending)}  ({rate:.1f}/s, {errors} errores)")

    if not dry_run:
        con.commit()
        derive(con, log=log)
    log(f"bootstrap terminado: {done} ok, {errors} errores")
    con.close()


def validate(con):
    """Compara los labels del LLM con los que habia antes del bootstrap."""
    path = config.DATA_DIR / "labels_before_bootstrap.json"
    if not path.exists():
        return None
    before = json.loads(path.read_text(encoding="utf-8"))
    rows = con.execute(
        "SELECT id, label FROM articles WHERE llm_relevance IS NOT NULL"
    ).fetchall()
    agree = total = 0
    for row in rows:
        old = before.get(str(row["id"]))
        if old is None:
            continue
        total += 1
        agree += int(int(old) == int(row["label"]))
    return {"total": total, "agree": agree, "rate": agree / total if total else 0.0}
