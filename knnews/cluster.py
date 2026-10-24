"""Clustering por evento (mismo hecho en varias fuentes) y saliencia."""

import time

import numpy as np

from . import config, store

MAX_WINDOW_ARTICLES = 2500


def _unit(matrix):
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _find(parent, i):
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _union(parent, a, b):
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[rb] = ra


def assign_clusters(con, settings, log=print):
    """Agrupa articulos recientes por similitud y anota nº de fuentes distintas."""
    window_h = float(settings.get("cluster_window_hours", 72))
    thr = float(settings.get("salience_similarity", 0.80))
    since = int(time.time() - window_h * 3600)

    rows = store.recent_vectors(con, since)[:MAX_WINDOW_ARTICLES]
    n = len(rows)
    if n == 0:
        return 0
    if n == 1:
        row = rows[0]
        con.execute(
            "UPDATE articles SET cluster_id=?, cluster_sources=1 WHERE id=?",
            (f"c{row['id']}", row["id"]),
        )
        con.commit()
        return 1

    ids = [row["id"] for row in rows]
    sources = [row["source"] for row in rows]
    matrix = _unit(
        np.vstack([store.blob_to_vector(row["vector"]) for row in rows]).astype(np.float32)
    )

    parent = list(range(n))
    sims = matrix @ matrix.T
    iu = np.triu_indices(n, k=1)
    matched = np.nonzero(sims[iu] >= thr)[0]
    for pos in matched:
        _union(parent, int(iu[0][pos]), int(iu[1][pos]))

    components = {}
    for i in range(n):
        components.setdefault(_find(parent, i), []).append(i)

    for members in components.values():
        cluster_id = f"c{min(ids[i] for i in members)}"
        distinct = len({sources[i] for i in members})
        con.executemany(
            "UPDATE articles SET cluster_id=?, cluster_sources=? WHERE id=?",
            [(cluster_id, distinct, ids[i]) for i in members],
        )
    con.commit()
    log(f"clusters: {len(components)} eventos para {n} articulos recientes")
    return len(components)
