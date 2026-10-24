"""Lectura de feeds y vectorizacion de articulos nuevos."""

import calendar
import html
import re
import socket
import time
import urllib.error
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import feedparser

from . import config, embed, fulltext, store

TAG_RE = re.compile(r'^(?P<url>\S+)(?:\s+"~(?P<tag>.+)")?\s*$')
TAGS_RE = re.compile(r"<[^>]+>")
SPACES_RE = re.compile(r"\s+")
TEXT_LIMIT = 2000


def parse_urls(path=None):
    """Lee el archivo estilo newsboat: URL "~tag" (tag opcional)."""
    path = Path(path or config.URLS_FILE)
    feeds = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = TAG_RE.match(line)
        if not match:
            continue
        url = match.group("url")
        tag = match.group("tag") or url
        feeds.append((url, tag))
    return feeds


def clean_html(value):
    if not value:
        return ""
    text = TAGS_RE.sub(" ", value)
    text = html.unescape(text)
    return SPACES_RE.sub(" ", text).strip()


def _timestamp(entry):
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return calendar.timegm(parsed)
            except (TypeError, ValueError):
                pass
    return int(time.time())


def fetch_feed(url, tag):
    socket.setdefaulttimeout(config.FETCH_TIMEOUT)
    parsed = feedparser.parse(url, agent=config.USER_AGENT)
    max_chars = config.settings().get("fulltext_max_chars", 300000)
    items = []
    for entry in parsed.entries:
        link = entry.get("link") or ""
        guid = link or entry.get("id") or entry.get("title")
        if not guid:
            continue
        title = clean_html(entry.get("title"))
        if not title:
            continue

        content_html = ""
        if entry.get("content"):
            content_html = entry["content"][0].get("value", "")
        if not content_html:
            content_html = entry.get("summary", "")
        content_html = content_html[:max_chars]

        summary = clean_html(entry.get("summary"))
        if not summary and content_html:
            summary = clean_html(content_html)

        text = f"{title}\n{summary}".strip()[:TEXT_LIMIT]
        items.append(
            {
                "guid": guid,
                "source": tag,
                "title": title,
                "link": link,
                "summary": summary[:1200],
                "content": content_html,
                "published": entry.get("published") or entry.get("updated") or "",
                "ts": _timestamp(entry),
                "fetched_at": store.now(),
                "text": text,
            }
        )
    return items


def ingest_all(con, log=print):
    """Descarga todos los feeds e inserta articulos nuevos. Devuelve el conteo."""
    inserted = 0
    feeds = parse_urls()
    log(f"leyendo {len(feeds)} feeds de {config.URLS_FILE}")
    for url, tag in feeds:
        try:
            items = fetch_feed(url, tag)
        except Exception as exc:  # noqa: BLE001 - una fuente rota no debe abortar
            log(f"  ! {tag}: {exc}")
            continue
        new = sum(1 for art in items if store.upsert_article(con, art))
        inserted += new
        log(f"  {tag}: {len(items)} items, {new} nuevos")
    log(f"ingesta completa: {inserted} articulos nuevos")
    return inserted


def embed_pending(con, log=print):
    """Vectoriza los articulos que aun no tienen embedding."""
    rows = store.articles_without_vector(con)
    if not rows:
        log("no hay articulos pendientes de vectorizar")
        return 0
    log(f"vectorizando {len(rows)} articulos")
    vectors = embed.embed_texts([row["text"] for row in rows], log=log)
    for row, vec in zip(rows, vectors):
        store.set_vector(con, row["id"], vec)
    log(f"vectorizados {len(rows)} articulos")
    return len(rows)


def enrich_fulltext(con, log=print):
    """Descarga el articulo completo de las fuentes que solo dan encabezado."""
    settings = config.settings()
    if not settings.get("fulltext", True):
        return 0
    min_chars = int(settings.get("fulltext_min_chars", 900))
    rows = store.articles_needing_fulltext(con, min_chars, limit=400)
    if not rows:
        log("no hay articulos que necesiten texto completo")
        return 0

    by_host = defaultdict(list)
    for row in rows:
        host = urlsplit(row["link"]).netloc.lower()
        by_host[host].append(row)

    log(f"buscando texto completo: {len(rows)} pendientes en {len(by_host)} hosts")
    fetched = 0
    attempted = 0
    per_host = 25
    for host, items in by_host.items():
        delay = 2.5 if "phys.org" in host else 0.8
        for row in items[:per_host]:
            attempted += 1
            body = None
            try:
                body = fulltext.fetch_article(row["link"])
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    log(f"  {host}: 429 rate-limit, se pospone al proximo ciclo")
                    break
            except Exception:  # noqa: BLE001 - un fallo puntual no aborta
                body = None
            existing = row.get("content") or ""
            if body and len(body) > len(existing):
                store.record_fulltext(con, row["id"], body, True)
                fetched += 1
            elif body:
                # la pagina no aporta mas que el feed: damos por hecho el articulo
                store.record_fulltext(con, row["id"], None, True)
            else:
                # fallo transitorio: se reintenta en ciclos posteriores (max 5)
                store.record_fulltext(con, row["id"], None, False)
            time.sleep(delay)
    store.commit(con)
    log(f"texto completo obtenido en {fetched}/{attempted} intentos")
    return fetched
