"""Cliente del servicio de embeddings (embed.ozkr.net)."""

import json
import time
import urllib.error
import urllib.request

import numpy as np

from . import config


def _post(batch):
    payload = json.dumps({"texts": batch}).encode("utf-8")
    req = urllib.request.Request(
        config.EMBED_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": config.USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=config.EMBED_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("success") or not data.get("embeddings"):
        raise RuntimeError(f"respuesta embed invalida: {data}")
    return data["embeddings"]


def embed_texts(texts, log=print):
    """Devuelve un array float32 (N, dims) para la lista de textos."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)

    out = []
    total = len(texts)
    for start in range(0, total, config.EMBED_BATCH):
        chunk = texts[start : start + config.EMBED_BATCH]
        batch = []
        for text in chunk:
            cleaned = (text or "").strip()[: config.EMBED_MAX_CHARS]
            batch.append(cleaned if cleaned else " ")
        last_error = None
        for attempt in range(4):
            try:
                out.extend(_post(batch))
                break
            except Exception as exc:  # noqa: BLE001 - reintentamos cualquier fallo
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        else:
            raise RuntimeError(f"embed fallo en lote {start}: {last_error}")
        if total > config.EMBED_BATCH:
            log(f"  embebidos {min(start + config.EMBED_BATCH, total)}/{total}")

    return np.asarray(out, dtype=np.float32)
