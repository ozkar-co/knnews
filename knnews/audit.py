"""Auditoria de fuentes: cuanto texto entrega realmente cada feed."""

import statistics
from concurrent.futures import ThreadPoolExecutor

import feedparser

from . import config, ingest


def _measure(item):
    url, tag = item
    try:
        parsed = feedparser.parse(url, agent=config.USER_AGENT)
    except Exception as exc:  # noqa: BLE001
        return {"tag": tag, "url": url, "verdict": "ERROR", "items": 0,
                "content_frac": 0.0, "median": 0, "note": str(exc)}
    entries = parsed.entries
    if not entries:
        return {"tag": tag, "url": url, "verdict": "SIN ENTRADAS", "items": 0,
                "content_frac": 0.0, "median": 0, "note": ""}
    with_content = 0
    text_lens = []
    for entry in entries:
        content = entry.get("content")
        if content:
            with_content += 1
            html = content[0].get("value", "")
        else:
            html = entry.get("summary", "")
        text_lens.append(len(ingest.clean_html(html)))
    median = int(statistics.median(text_lens))
    if median >= 1200:
        verdict = "COMPLETO"
    elif median >= 400:
        verdict = "PARCIAL"
    else:
        verdict = "ENCABEZADO"
    return {"tag": tag, "url": url, "verdict": verdict, "items": len(entries),
            "content_frac": round(with_content / len(entries), 2), "median": median,
            "note": ""}


def run(log=print):
    feeds = ingest.parse_urls()
    with ThreadPoolExecutor(max_workers=16) as pool:
        rows = list(pool.map(_measure, feeds))
    order = {"ENCABEZADO": 0, "PARCIAL": 1, "COMPLETO": 2, "SIN ENTRADAS": 3, "ERROR": 4}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9), r["median"]))
    log(f"{'veredicto':<11} {'items':>5} {'%content':>8} {'med.texto':>9}  fuente")
    for row in rows:
        log(f"{row['verdict']:<11} {row['items']:>5} {row['content_frac']:>8} "
            f"{row['median']:>9}  {row['tag']}")
    truncated = [r["tag"] for r in rows if r["verdict"] in ("ENCABEZADO", "PARCIAL")]
    if truncated:
        log("")
        log("Aviso: estas fuentes no traen el texto completo; knnews intentara "
            "extraerlo de la pagina del articulo (fulltext):")
        log("  " + ", ".join(truncated))
    return rows
