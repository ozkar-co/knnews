"""Generacion de los RSS de salida (uno por tema + combinado)."""

import json
from email.utils import formatdate

from . import config, store


def _esc(value):
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _cdata(value):
    return "<![CDATA[" + (value or "").replace("]]>", "]]]]><![CDATA[>") + "]]>"


def build_rss(channel_title, channel_desc, self_link, items):
    now = formatdate(localtime=False, usegmt=True)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{_esc(channel_title)}</title>",
        f"<link>{_esc(self_link)}</link>",
        f"<description>{_esc(channel_desc)}</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        f'<atom:link href="{_esc(self_link)}" rel="self" type="application/rss+xml"/>',
    ]
    for art in items:
        pub = (
            formatdate(art["ts"], localtime=False, usegmt=True)
            if art.get("ts")
            else now
        )
        score = art.get("score") or 0.0
        body = f"<p>{_esc(art.get('summary'))}</p>" if art.get("summary") else ""
        body += (
            f"<p><small>[{_esc(art.get('source'))} · "
            f"{_esc(art.get('topic'))} · {score:.2f}]</small></p>"
        )
        out += [
            "<item>",
            f"<title>{_esc(art.get('title'))}</title>",
            f"<link>{_esc(art.get('link'))}</link>",
            f'<guid isPermaLink="false">{_esc(art.get("guid"))}</guid>',
            f"<pubDate>{pub}</pubDate>",
            f"<description>{_cdata(body)}</description>",
            "</item>",
        ]
    out += ["</channel>", "</rss>"]
    return "\n".join(out)


def publish(con, settings, log=print):
    config.FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    threshold = float(settings.get("threshold", 0.35))
    limit = int(settings.get("feeds_limit", 80))
    base = settings["base_url"].rstrip("/")

    articles = store.for_feed(con, threshold, limit)
    by_topic = {}
    for art in articles:
        by_topic.setdefault(art.get("topic") or "otros", []).append(art)

    manifest = []

    def _write(slug, title, desc, items):
        path = config.FEEDS_DIR / f"{slug}.xml"
        path.write_text(build_rss(title, desc, f"{base}/feeds/{slug}.xml", items), encoding="utf-8")
        manifest.append(
            {
                "slug": slug,
                "title": title,
                "url": f"{base}/feeds/{slug}.xml",
                "count": len(items),
            }
        )
        log(f"  feed {slug}: {len(items)} articulos")

    _write(
        "todo",
        "knnews · todo relevante",
        "Articulos filtrados por encima del umbral, todos los temas.",
        articles,
    )
    for topic in settings["topics"]:
        slug = topic["slug"]
        items = by_topic.get(slug, [])
        if not items:
            continue
        _write(
            f"tema-{slug}",
            f"knnews · {topic['name']}",
            f"Articulos relevantes de {topic['name']}.",
            items,
        )

    config.MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"publicados {len(manifest)} feeds en {config.FEEDS_DIR}")
    return manifest
