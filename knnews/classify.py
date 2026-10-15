"""Perfil de interes y clasificacion (anclas de tema + KNN sobre etiquetas)."""

import hashlib
import json

import numpy as np

from . import config, embed, store


def _normalize_rows(matrix):
    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def topic_vectors(settings, log=print):
    """Vectores normalizados de las descripciones de cada tema (con cache)."""
    topics = settings["topics"]
    descriptions = [t["description"] for t in topics]
    digest = hashlib.sha256("\n".join(descriptions).encode("utf-8")).hexdigest()

    cached = None
    if config.PROFILE_PATH.exists():
        try:
            cached = json.loads(config.PROFILE_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            cached = None

    if cached and cached.get("digest") == digest:
        matrix = np.asarray(cached["vectors"], dtype=np.float32)
    else:
        matrix = embed.embed_texts(descriptions, log=log)
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.PROFILE_PATH.write_text(
            json.dumps({"digest": digest, "vectors": matrix.tolist()}),
            encoding="utf-8",
        )
    return _normalize_rows(matrix)


def knn_score(vector, labeled_labels, labeled_matrix, k):
    """Fraccion ponderada por similitud de vecinos etiquetados como relevantes."""
    if labeled_matrix.shape[0] == 0:
        return None
    sims = labeled_matrix @ vector
    k = min(k, sims.shape[0])
    idx = np.argpartition(-sims, k - 1)[:k]
    weights = np.clip(sims[idx], 0.0, None)
    labels = labeled_labels[idx]
    pos = float(weights[labels > 0].sum())
    neg = float(weights[labels < 0].sum())
    if pos + neg <= 0:
        # sin similitud positiva: decide por mayoria simple de los vecinos
        return float((labels > 0).sum()) / labels.size
    return pos / (pos + neg)


def classify_all(con, settings, only_unclassified=True, log=print):
    """Asigna tema, score y componentes a cada articulo con vector."""
    ids = store.articles_to_classify(con, only_unclassified=only_unclassified)
    if not ids:
        log("nada por clasificar")
        return 0

    topic_matrix = topic_vectors(settings, log=log)
    labels, labeled_matrix = store.labeled_examples(con)
    labeled_matrix = _normalize_rows(labeled_matrix)
    has_labels = labeled_matrix.shape[0] > 0

    floor = float(settings.get("topic_floor", 0.0))
    k = int(settings.get("knn_k", 15))
    w_knn = float(settings.get("weight_knn", 0.65))
    w_anchor = float(settings.get("weight_anchor", 0.35))

    for article_id in ids:
        vector = store.get_vector(con, article_id)
        if vector is None:
            continue
        norm = np.linalg.norm(vector)
        unit = vector / norm if norm else vector

        sims = topic_matrix @ unit
        best = int(np.argmax(sims))
        anchor = float(sims[best])
        topic = settings["topics"][best]["slug"] if anchor >= floor else "otros"

        knn = knn_score(unit, labels, labeled_matrix, k) if has_labels else None
        if knn is None:
            score = anchor
        else:
            score = w_knn * knn + w_anchor * anchor

        store.set_classification(con, article_id, topic, float(score), anchor, knn)

    store.commit(con)
    log(f"clasificados {len(ids)} articulos ({'con' if has_labels else 'sin'} etiquetas)")
    return len(ids)
