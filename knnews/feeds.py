"""Generacion de los RSS de salida (mismos feeds que ya usas)."""

import json
import re
from email.utils import formatdate

from . import config, store

STOPWORDS = {
    "para", "como", "este", "esta", "esto", "sobre", "desde", "hasta", "entre",
    "cuando", "donde", "porque", "pero", "mas", "más", "los", "las", "del",
    "que", "con", "por", "una", "uno", "unos", "unas", "sus", "sin", "son",
}

AD_RE = re.compile(
    r"^(EMPLEO|VENTA|ARRIENDO|COMPRO|BUSCO|OFERTA|SERVICIO|CASA|APARTAMENTO|"
    r"LOTE|TERRENO|PORTADA|EDICION)\b",
    re.IGNORECASE,
)


def _tokens(title):
    words = re.findall(r"[a-záéíóúñü0-9]{4,}", (title or "").lower())
    return {word for word in words if word not in STOPWORDS}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


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
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/">',
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
        importance = art.get("importance") or 0.0
        foot = (
            f"<p><small>[{_esc(art.get('source'))} · "
            f"{_esc(art.get('topic'))} · rel {score:.2f} · imp {importance:.2f}]"
            "</small></p>"
        )
        summary = f"<p>{_esc(art.get('summary'))}</p>" if art.get("summary") else ""
        full = art.get("content") or summary
        out += [
            "<item>",
            f"<title>{_esc(art.get('title'))}</title>",
            f"<link>{_esc(art.get('link'))}</link>",
            f'<guid isPermaLink="false">{_esc(art.get("guid"))}</guid>',
            f"<pubDate>{pub}</pubDate>",
            f"<description>{_cdata(summary + foot)}</description>",
            f"<content:encoded>{_cdata(full + foot)}</content:encoded>",
            "</item>",
        ]
    out += ["</channel>", "</rss>"]
    return "\n".join(out)


def _decorate(art):
    try:
        flags = json.loads(art.get("flags") or "{}")
    except (ValueError, TypeError):
        flags = {}
    if flags.get("hard_excluded"):
        return None
    art["_combined"] = 0.6 * (art.get("score") or 0.0) + 0.4 * (art.get("importance") or 0.0)
    return art


def _prepare(candidates):
    """Descarta excluidos, deduplica por evento y puntua para ordenar."""
    best = {}
    for raw in candidates:
        art = _decorate(raw)
        if art is None:
            continue
        key = art.get("cluster_id") or f"a{art['id']}"
        current = best.get(key)
        if current is None or art["_combined"] > current["_combined"]:
            best[key] = art
    ranked = sorted(
        best.values(),
        key=lambda a: (a["_combined"], a.get("ts") or 0),
        reverse=True,
    )
    # dedup por titulo (sindicacion / mismo hecho con otra redaccion)
    kept = []
    kept_tokens = []
    for art in ranked:
        toks = _tokens(art.get("title"))
        if any(_jaccard(toks, other) >= 0.55 for other in kept_tokens):
            continue
        kept.append(art)
        kept_tokens.append(toks)
    return kept


def _take(items, limit, max_per_source):
    out = []
    counts = {}
    for art in items:
        source = art.get("source") or "?"
        if counts.get(source, 0) >= max_per_source:
            continue
        counts[source] = counts.get(source, 0) + 1
        out.append(art)
        if len(out) >= limit:
            break
    return out


def publish(con, settings, log=print):
    config.FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    threshold = float(settings.get("threshold", 0.32))
    importance_threshold = float(settings.get("importance_threshold", 0.50))
    limit = int(settings.get("feeds_limit", 80))
    max_per_source = int(settings.get("max_per_source", 8))
    base = settings["base_url"].rstrip("/")

    ranked = _prepare(store.feed_candidates(con, threshold, importance_threshold))
    by_topic = {}
    for art in ranked:
        by_topic.setdefault(art.get("topic") or "otros", []).append(art)

    manifest = []

    def _write(slug, title, desc, items):
        path = config.FEEDS_DIR / f"{slug}.xml"
        path.write_text(
            build_rss(title, desc, f"{base}/feeds/{slug}.xml", items), encoding="utf-8"
        )
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
        "Articulos relevantes e importantes, todos los temas.",
        _take(ranked, limit, max_per_source),
    )
    local_sources = settings.get("local_sources") or []
    local_per_source = int(settings.get("local_per_source", 3))
    for topic in settings["topics"]:
        slug = topic["slug"]
        items = list(by_topic.get(slug, []))
        if slug == "colombia" and local_sources:
            local = []
            for raw in store.local_candidates(con, local_sources, 400):
                art = _decorate(raw)
                if art is None:
                    continue
                title = art.get("title") or ""
                if AD_RE.match(title) or len(title.split()) < 3:
                    continue
                if art["_combined"] < 0.12:
                    continue
                lr = art.get("llm_relevance") or 0
                if lr < 2 and (art.get("importance") or 0) < 0.35:
                    continue
                try:
                    llm_flags = json.loads(art.get("llm_flags") or "{}")
                except (ValueError, TypeError):
                    llm_flags = {}
                if not llm_flags.get("local_pereira") and (art.get("importance") or 0) < 0.35:
                    continue
                local.append(art)
            local.sort(
                key=lambda a: (a["_combined"], a.get("ts") or 0), reverse=True
            )
            counts = {}
            picked = []
            for art in local:
                source = art.get("source")
                if counts.get(source, 0) >= local_per_source:
                    continue
                counts[source] = counts.get(source, 0) + 1
                picked.append(art)
            items = _prepare(list(items) + picked)
        selected = _take(items, limit, max_per_source)
        _write(
            f"tema-{slug}",
            f"knnews · {topic['name']}",
            f"Articulos de {topic['name']}.",
            selected,
        )

    config.MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    keep = {f"{entry['slug']}.xml" for entry in manifest}
    for stale in config.FEEDS_DIR.glob("*.xml"):
        if stale.name not in keep:
            stale.unlink()
    log(f"publicados {len(manifest)} feeds en {config.FEEDS_DIR}")
    return manifest
