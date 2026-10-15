"""Servidor HTTP de los feeds generados (para el tunel Cloudflare)."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config

FEED_RE = re.compile(r"^/feeds/([A-Za-z0-9_.-]+\.xml)$")


def _read_manifest():
    if config.MANIFEST_PATH.exists():
        try:
            return json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []
    return []


class Handler(BaseHTTPRequestHandler):
    server_version = "knnews/0.1"

    def log_message(self, fmt, *args):  # silenciar log por peticion
        pass

    def _send(self, code, body, content_type):
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 - API de http.server
        path = self.path.split("?", 1)[0]

        if path == "/health":
            self._send(200, json.dumps({"status": "ok"}), "application/json")
            return

        if path in ("/", "/index.html"):
            self._send(200, self._index(), "text/html; charset=utf-8")
            return

        match = FEED_RE.match(path)
        if match:
            feed_path = config.FEEDS_DIR / match.group(1)
            if feed_path.exists():
                self._send(200, feed_path.read_bytes(), "application/rss+xml; charset=utf-8")
                return
            self._send(404, "feed no encontrado", "text/plain; charset=utf-8")
            return

        self._send(404, "no encontrado", "text/plain; charset=utf-8")

    def _index(self):
        rows = []
        for entry in _read_manifest():
            rows.append(
                f'<li><a href="/feeds/{entry["slug"]}.xml">{entry["title"]}</a> '
                f'<small>({entry["count"]} articulos)</small></li>'
            )
        body = "\n".join(rows) or "<li>Sin feeds todavia. Corre un ciclo.</li>"
        return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>knnews</title></head>
<body style="font-family:system-ui;max-width:720px;margin:2rem auto">
<h1>knnews</h1>
<p>Feeds filtrados. Suscribelos en newsboat con estas URLs.</p>
<ul>{body}</ul>
<p><a href="/health">/health</a></p>
</body></html>"""


def serve(settings=None):
    settings = settings or config.settings()
    host = settings.get("host", config.HOST)
    port = int(settings.get("port", 3010))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"knnews sirviendo en http://{host}:{port} (feeds en /feeds/*.xml)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ndeteniendo...")
    finally:
        httpd.server_close()
