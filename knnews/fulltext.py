"""Extraccion de texto completo desde la pagina del articulo.

Se usa como respaldo para las fuentes que solo publican un encabezado. Funciona
con la libreria estandar: descarga el HTML y toma los parrafos del cuerpo del
articulo (`<article>`/`<main>` si existen, si no, todos los parrafos largos).
"""

import html as html_lib
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from . import config

SKIP_TAGS = {"script", "style", "noscript", "template"}
BLOCK_TAGS = {"article", "main"}
MIN_PARAGRAPH = 40
BOILERPLATE_RE = re.compile(
    r"^(edited by|this article has been reviewed|editors have highlighted|"
    r"science x|provided by|citation:|doi:|journal information|explore further|"
    r"more information|this document is subject to copyright|"
    r"apart from any fair dealing|the content is provided for information|"
    r"reprinted|image credit|credit:|story source|source:|"
    r"read more|read the full|read the entire|read the complete|full text|"
    r"continue reading|full story|leer m[aá]s|sigue leyendo|contin[uú]a leyendo|"
    r"posts from this (topic|author)|will be added to your daily email digest|"
    r"daily email digest|homepage feed|sign up|subscribe|newsletter|"
    r"follow us|share this|advertisement|related stories|most popular|"
    r"more from|read next|you might also like|recommended|trending|"
    r"about the author|join the conversation|tags:|"
    r"a free daily digest|daily digest of the news|news that matters most|"
    r"sign up for our|download our app|follow us on|"
    r"the post .* appeared first)",
    re.IGNORECASE,
)

CTA_RE = re.compile(
    r"(read (the )?(full|more|entire|rest|complete)|continue reading|full story|"
    r"leer (m[aá]s|la nota|el art[ií]culo|completo)|sigue leyendo|"
    r"contin[uú]a leyendo|ver (la )?nota completa|posts from this topic|"
    r"posts from this author|daily email digest|homepage feed|newsletter|"
    r"sign up for|subscribe to)",
    re.IGNORECASE,
)


def text_length(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return len(" ".join(text.split()))


def strip_cta(html):
    """Quita parrafos/enlaces tipo 'read more / leer mas' del contenido."""
    if not html:
        return ""

    def repl_paragraph(match):
        inner = match.group(1)
        text = " ".join(re.sub(r"<[^>]+>", " ", inner).split())
        if CTA_RE.search(text) and len(text) < 200:
            return ""
        return match.group(0)

    html = re.sub(r"<p[^>]*>(.*?)</p>", repl_paragraph, html, flags=re.I | re.S)
    html = re.sub(
        r"<a[^>]*>([^<]{0,120})</a>",
        lambda m: "" if CTA_RE.search(m.group(1) or "") else m.group(0),
        html,
        flags=re.I,
    )
    return html


MEDIA_BLOCK_RE = re.compile(
    r"<(script|style|noscript|iframe|video|audio|svg|picture|figure|object)\b[^>]*>"
    r".*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
MEDIA_TAG_RE = re.compile(
    r"<(img|source|track|embed|object|picture)\b[^>]*>", re.IGNORECASE
)
ANCHOR_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
EMPTY_ANCHOR_RE = re.compile(r"<a\b[^>]*>\s*</a>", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
EMPTY_TAG_RE = re.compile(
    r"<(p|div|span|li|ul|ol|h[1-6]|section|article)\b[^>]*>\s*</\1>",
    re.IGNORECASE,
)


def sanitize_html(html):
    """Deja solo texto: sin imagenes/video ni enlaces (conserva el texto)."""
    if not html:
        return ""
    html = COMMENT_RE.sub("", html)
    html = MEDIA_BLOCK_RE.sub("", html)
    html = MEDIA_TAG_RE.sub("", html)
    html = ANCHOR_RE.sub(r"\1", html)
    html = EMPTY_ANCHOR_RE.sub("", html)
    html = URL_RE.sub("", html)
    for _ in range(2):
        html = EMPTY_TAG_RE.sub("", html)
    html = html.replace("&nbsp;", " ")
    html = re.sub(r">\s+<", "><", html)
    return html.strip()


RELATED_RE = re.compile(
    r"^(related|more from|read next|you might also like|recommended|share|follow|"
    r"subscribe|sign up|advertisement|tags|about the author|comments|"
    r"join the conversation|posts from this (topic|author)|daily email digest|"
    r"homepage feed|most popular|trending|popular|newsletter|"
    r"a free daily digest|daily digest of the news|news that matters most)\b",
    re.IGNORECASE,
)
BLOCK_TAGS = {"p", "pre", "blockquote", "li", "h2", "h3", "h4"}


class _BlockExtractor(HTMLParser):
    """Extrae bloques de contenido (parrafos, codigo, listas) de un HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._in_link = 0
        self._block = None
        self._depth = 0
        self._text = []
        self._link_len = 0
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag == "a":
            self._in_link += 1
        if tag in BLOCK_TAGS:
            if self._block is None:
                self._block = tag
                self._text = []
                self._link_len = 0
                self._depth = 1
            else:
                self._depth += 1

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            if self._skip:
                self._skip -= 1
            return
        if tag == "a" and self._in_link:
            self._in_link -= 1
        if tag in BLOCK_TAGS and self._block is not None:
            self._depth -= 1
            if self._depth == 0:
                self.blocks.append(
                    (self._block, "".join(self._text), self._link_len)
                )
                self._block = None

    def handle_data(self, data):
        if self._skip or self._block is None:
            return
        self._text.append(data)
        if self._in_link:
            self._link_len += len(data)


def clean_content(html):
    """Conserva solo el texto de los bloques del articulo.

    Elimina imagenes, enlaces, relacionados, newsletters y cualquier bloque
    que sea mayoritariamente un enlace o boilerplate. Conserva codigo (<pre>).
    """
    if not html:
        return ""
    parser = _BlockExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - HTML roto no debe romper el feed
        pass

    kept = []
    seen = set()
    for tag, raw, link_len in parser.blocks:
        if tag == "pre":
            text = raw.strip("\n")
            if len(text.strip()) < 20 or text in seen:
                continue
            seen.add(text)
            kept.append(f"<pre>{html_lib.escape(text)}</pre>")
            continue
        text = " ".join(raw.split())
        if len(text) < 25 or text in seen:
            continue
        if BOILERPLATE_RE.match(text) or RELATED_RE.match(text):
            continue
        if link_len and link_len / max(1, len(text)) > 0.5:
            continue
        seen.add(text)
        kept.append(f"<p>{html_lib.escape(text)}</p>")

    if not parser.blocks:
        return sanitize_html(html)
    return "\n".join(kept)


def _is_public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _validate_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"esquema no permitido: {parts.scheme}")
    if not parts.hostname or not _is_public_host(parts.hostname):
        raise ValueError(f"host no permitido: {parts.hostname}")
    return url


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
            if len(text) >= MIN_PARAGRAPH and not BOILERPLATE_RE.match(text):
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
    _validate_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    opener = urllib.request.build_opener(_SafeRedirect)
    with opener.open(req, timeout=timeout) as resp:
        _validate_url(resp.geturl())
        raw = resp.read(2_000_000)
        encoding = resp.headers.get_content_charset() or "utf-8"
    html = raw.decode(encoding, "replace")
    paragraphs = extract_paragraphs(html)
    if not paragraphs:
        return None
    body = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    return body[: config.settings().get("fulltext_max_chars", 300000)]
