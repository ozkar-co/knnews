"""Extraccion de texto completo desde la pagina del articulo.

Se usa como respaldo para las fuentes que solo publican un encabezado. Funciona
con la libreria estandar: descarga el HTML y toma los parrafos del cuerpo del
articulo (`<article>`/`<main>` si existen, si no, todos los parrafos largos).
"""

import urllib.request
from html.parser import HTMLParser

from . import config

SKIP_TAGS = {"script", "style", "noscript", "template"}
BLOCK_TAGS = {"article", "main"}
MIN_PARAGRAPH = 40


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._depth_article = 0
        self._in_p = False
        self._buffer = []
        self.all_paragraphs = []
        self.article_paragraphs = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag in BLOCK_TAGS:
            self._depth_article += 1
        elif tag == "p":
            self._in_p = True
            self._buffer = []

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            if self._skip:
                self._skip -= 1
            return
        if tag == "p" and self._in_p:
            self._in_p = False
            text = " ".join("".join(self._buffer).split())
            if len(text) >= MIN_PARAGRAPH:
                self.all_paragraphs.append(text)
                if self._depth_article > 0:
                    self.article_paragraphs.append(text)
            self._buffer = []
        elif tag in BLOCK_TAGS and self._depth_article > 0:
            self._depth_article -= 1

    def handle_data(self, data):
        if self._in_p and not self._skip:
            self._buffer.append(data)


def extract_paragraphs(html):
    parser = _Extractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - HTML roto no debe tumbar el ciclo
        pass
    paragraphs = parser.article_paragraphs
    if sum(len(p) for p in paragraphs) < 600:
        paragraphs = parser.all_paragraphs
    return paragraphs


def fetch_article(url, timeout=None):
    """Devuelve HTML `<p>...</p>` del cuerpo, o None si no se pudo."""
    timeout = timeout or config.settings().get("fulltext_timeout", 20)
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(2_000_000)
        encoding = resp.headers.get_content_charset() or "utf-8"
    html = raw.decode(encoding, "replace")
    paragraphs = extract_paragraphs(html)
    if not paragraphs:
        return None
    body = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    return body[: config.settings().get("fulltext_max_chars", 300000)]
