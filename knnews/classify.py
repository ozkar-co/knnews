"""Clasificacion en dos ejes: gusto (KNN/anclas) + importancia (saliencia)."""

import hashlib
import json
import math
import time

import numpy as np

from . import config, embed, store


def _normalize_rows(matrix):
    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def profile_vectors(settings, log=print):
    """Vectores normalizados de anclas de tema y de descartados (con cache)."""
    topics = settings["topics"]
    dislikes = settings.get("dislikes") or []
    topic_desc = [t["description"] for t in topics]
    digest = hashlib.sha256(
        ("\n".join(topic_desc) + "\n--\n" + "\n".join(dislikes)).encode("utf-8")
    ).hexdigest()

    cached = None
    if config.PROFILE_PATH.exists():
        try:
            cached = json.loads(config.PROFILE_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            cached = None

    if cached and cached.get("digest") == digest and "topics" in cached:
        topic_matrix = np.asarray(cached["topics"], dtype=np.float32)
        dislike_matrix = np.asarray(cached.get("dislikes", []), dtype=np.float32)
        if dislike_matrix.size == 0:
            dislike_matrix = np.zeros((0, topic_matrix.shape[1]), dtype=np.float32)
    else:
        topic_matrix = embed.embed_texts(topic_desc, log=log)
        if dislikes:
            dislike_matrix = embed.embed_texts(dislikes, log=log)
        else:
            dislike_matrix = np.zeros((0, topic_matrix.shape[1]), dtype=np.float32)
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.PROFILE_PATH.write_text(
            json.dumps(
                {
                    "digest": digest,
                    "topics": topic_matrix.tolist(),
                    "dislikes": dislike_matrix.tolist(),
                }
            ),
            encoding="utf-8",
        )
    return _normalize_rows(topic_matrix), _normalize_rows(dislike_matrix)


def knn_score(vector, labeled_labels, labeled_matrix, k):
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
        return float((labels > 0).sum()) / labels.size
    return pos / (pos + neg)


def _salience(n_sources, max_sources):
    if not n_sources or n_sources <= 1:
        return 0.0
    return min(1.0, (n_sources - 1) / max(1, max_sources - 1))


def _recency(ts):
    age_hours = max(0.0, (time.time() - float(ts or 0)) / 3600.0)
    return math.exp(-age_hours / 48.0)


def _importance(salience, source_prior, recency, settings):
    value = (
        float(settings.get("weight_salience", 0.55)) * salience
        + float(settings.get("weight_source_importance", 0.25)) * source_prior
        + float(settings.get("weight_recency", 0.20)) * recency
    )
    return max(0.0, min(1.0, value))


def update_importance(con, settings, log=print):
    """Recalcula solo la importancia de lo reciente (no toca gusto ni tema)."""
    window_h = float(settings.get("cluster_window_hours", 72))
    since = int(time.time() - window_h * 3600)
    rows = store.recent_for_importance(con, since)
    if not rows:
        return 0
    priors = store.load_source_importance(con)
    neutral = 2.5
    max_sources = int(settings.get("max_cluster_sources", 5))
    for row in rows:
        sal = _salience(row.get("cluster_sources"), max_sources)
        prior = min(1.0, (priors.get(row["source"], neutral)) / 5.0)
        imp = _importance(sal, prior, _recency(row["ts"]), settings)
        con.execute("UPDATE articles SET importance=? WHERE id=?", (imp, row["id"]))
    con.commit()
    log(f"importancia actualizada en {len(rows)} articulos recientes")
    return len(rows)


def classify_all(con, settings, only_unclassified=True, log=print):
    rows = store.articles_for_classification(con, only_unclassified=only_unclassified)
    if not rows:
        log("nada por clasificar")
        return 0

    topic_matrix, dislike_matrix = profile_vectors(settings, log=log)
    labels, labeled_matrix = store.labeled_examples(con)
    labeled_matrix = _normalize_rows(labeled_matrix)
    has_labels = labeled_matrix.shape[0] > 0

    domains = store.load_domains(con)
    domain_names = list(domains.keys())
    if domain_names:
        domain_matrix = _normalize_rows(
            np.vstack([domains[name] for name in domain_names]).astype(np.float32)
        )
    else:
        domain_matrix = np.zeros((0, 384), dtype=np.float32)
    hard = set(settings.get("hard_exclude_domains") or [])
    soft = set(settings.get("soft_domains") or [])
    local_sources = set(settings.get("local_sources") or [])
    domain_thr = float(settings.get("domain_threshold", 0.52))

    priors = store.load_source_importance(con)
    neutral = 2.5
    max_sources = int(settings.get("max_cluster_sources", 5))
    source_topic = settings.get("source_topic") or {}
    slugs = [t["slug"] for t in settings["topics"]]
    floor = float(settings.get("topic_floor", 0.10))
    k = int(settings.get("knn_k", 15))
    w_knn = float(settings.get("weight_knn", 0.65))
    w_anchor = float(settings.get("weight_anchor", 0.25))
    w_dislike = float(settings.get("weight_dislike", 0.35))
    soft_weight = 0.15

    for row in rows:
        vector = store.blob_to_vector(row["vector"])
        norm = np.linalg.norm(vector)
        unit = vector / norm if norm else vector

        sims = topic_matrix @ unit
        best = int(np.argmax(sims))
        anchor = float(sims[best])
        mapped = source_topic.get(row["source"])
        llm_topic = row.get("llm_topic")
        if llm_topic in slugs:
            topic = llm_topic
        elif mapped in slugs:
            topic = mapped
        elif anchor >= floor:
            topic = settings["topics"][best]["slug"]
        else:
            topic = "otros"

        dislike = 0.0
        if dislike_matrix.shape[0] > 0:
            dislike = max(0.0, float(np.max(dislike_matrix @ unit)))

        domain_scores = {}
        hard_hits = set()
        if domain_matrix.shape[0] > 0:
            ds = domain_matrix @ unit
            for name, value in zip(domain_names, ds):
                domain_scores[name] = float(value)
                if name in hard and domain_scores[name] >= domain_thr:
                    hard_hits.add(name)
        hard_excluded = bool(hard_hits)
        # las fuentes locales pueden cubrir crimen (Pereira/Eje Cafetero),
        # pero siguen excluidas para celebridades y deportes.
        if row["source"] in local_sources and hard_hits and hard_hits <= {"crime"}:
            hard_excluded = False
        soft_max = max((domain_scores.get(d, 0.0) for d in soft), default=0.0)

        knn = knn_score(unit, labels, labeled_matrix, k) if has_labels else None
        if knn is None:
            score = anchor - w_dislike * dislike - soft_weight * soft_max
        else:
            score = (
                w_knn * knn
                + w_anchor * anchor
                - w_dislike * dislike
                - soft_weight * soft_max
            )

        salience = _salience(row.get("cluster_sources"), max_sources)
        prior = min(1.0, priors.get(row["source"], neutral) / 5.0)
        importance = _importance(salience, prior, _recency(row["ts"]), settings)

        flags = json.dumps(
            {
                "domains": {key: round(value, 3) for key, value in domain_scores.items()},
                "hard_excluded": hard_excluded,
            },
            ensure_ascii=False,
        )

        store.set_classification(
            con,
            row["id"],
            topic,
            float(score),
            anchor,
            knn,
            dislike,
            importance,
            flags,
            None,
            row.get("cluster_sources"),
        )

    store.commit(con)
    log(f"clasificados {len(rows)} articulos ({'con' if has_labels else 'sin'} etiquetas)")
    return len(rows)
