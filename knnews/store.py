"""Persistencia SQLite: articulos, vectores y etiquetas."""

import sqlite3
from datetime import datetime, timezone

import numpy as np

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY,
    guid          TEXT UNIQUE,
    source        TEXT,
    title         TEXT,
    link          TEXT,
    summary       TEXT,
    published     TEXT,
    ts            INTEGER,
    fetched_at    TEXT,
    text          TEXT,
    vector        BLOB,
    topic         TEXT,
    score         REAL,
    anchor        REAL,
    knn           REAL,
    label         INTEGER,
    labeled_at    TEXT,
    classified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_label ON articles(label);
CREATE INDEX IF NOT EXISTS idx_articles_topic ON articles(topic);
CREATE INDEX IF NOT EXISTS idx_articles_score ON articles(score);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init(con):
    con.executescript(SCHEMA)
    con.commit()


def vector_to_blob(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def blob_to_vector(blob):
    return np.frombuffer(blob, dtype=np.float32)


def upsert_article(con, art, vector=None):
    """Inserta si el guid es nuevo. Devuelve True si se inserto."""
    blob = vector_to_blob(vector) if vector is not None else None
    cur = con.execute(
        """INSERT OR IGNORE INTO articles
           (guid, source, title, link, summary, published, ts, fetched_at, text, vector)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            art["guid"],
            art["source"],
            art["title"],
            art["link"],
            art["summary"],
            art["published"],
            art["ts"],
            art["fetched_at"],
            art["text"],
            blob,
        ),
    )
    con.commit()
    return cur.rowcount > 0


def articles_without_vector(con, limit=1000):
    return con.execute(
        "SELECT id, text FROM articles WHERE vector IS NULL ORDER BY ts DESC LIMIT ?",
        (limit,),
    ).fetchall()


def set_vector(con, article_id, vector):
    con.execute(
        "UPDATE articles SET vector = ? WHERE id = ?",
        (vector_to_blob(vector), article_id),
    )
    con.commit()


def articles_to_classify(con, only_unclassified=True, limit=None):
    sql = "SELECT id FROM articles WHERE vector IS NOT NULL"
    if only_unclassified:
        sql += " AND classified_at IS NULL"
    sql += " ORDER BY ts DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [row["id"] for row in con.execute(sql)]


def set_classification(con, article_id, topic, score, anchor, knn):
    con.execute(
        """UPDATE articles
           SET topic = ?, score = ?, anchor = ?, knn = ?, classified_at = ?
           WHERE id = ?""",
        (topic, score, anchor, knn, now(), article_id),
    )


def commit(con):
    con.commit()


def labeled_examples(con):
    """Devuelve (labels(+1/-1), matriz de vectores) de los articulos etiquetados."""
    rows = con.execute(
        "SELECT vector, label FROM articles WHERE label IS NOT NULL AND vector IS NOT NULL"
    ).fetchall()
    labels, vecs = [], []
    for row in rows:
        labels.append(1 if row["label"] == 1 else -1)
        vecs.append(blob_to_vector(row["vector"]))
    if not vecs:
        return np.array([], dtype=np.float32), np.zeros((0, 0), dtype=np.float32)
    return np.asarray(labels, dtype=np.float32), np.vstack(vecs).astype(np.float32)


def get_vector(con, article_id):
    row = con.execute("SELECT vector FROM articles WHERE id = ?", (article_id,)).fetchone()
    if not row or row["vector"] is None:
        return None
    return blob_to_vector(row["vector"])


def unlabeled(con, limit=25):
    return [
        dict(row)
        for row in con.execute(
            """SELECT id, source, title, link, summary, topic, score
               FROM articles
               WHERE label IS NULL AND vector IS NOT NULL
               ORDER BY ts DESC LIMIT ?""",
            (limit,),
        )
    ]


def set_label(con, article_id, label):
    con.execute(
        "UPDATE articles SET label = ?, labeled_at = ? WHERE id = ?",
        (label, now(), article_id),
    )
    con.commit()


def for_feed(con, min_score, limit):
    return [
        dict(row)
        for row in con.execute(
            """SELECT id, guid, source, title, link, summary, ts, topic, score
               FROM articles
               WHERE vector IS NOT NULL AND score IS NOT NULL AND score >= ?
               ORDER BY ts DESC LIMIT ?""",
            (min_score, limit),
        )
    ]


def stats(con):
    def one(sql, *args):
        return con.execute(sql, args).fetchone()[0]

    return {
        "articulos": one("SELECT COUNT(*) FROM articles"),
        "con_vector": one("SELECT COUNT(*) FROM articles WHERE vector IS NOT NULL"),
        "clasificados": one("SELECT COUNT(*) FROM articles WHERE classified_at IS NOT NULL"),
        "etiquetados": one("SELECT COUNT(*) FROM articles WHERE label IS NOT NULL"),
        "relevantes": one("SELECT COUNT(*) FROM articles WHERE label = 1"),
        "descartados": one("SELECT COUNT(*) FROM articles WHERE label = 0"),
    }
