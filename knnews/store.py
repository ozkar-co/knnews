"""Persistencia SQLite: articulos, vectores y etiquetas."""

import sqlite3
import time
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
    content       TEXT,
    fulltext_at   TEXT,
    vector        BLOB,
    topic         TEXT,
    score         REAL,
    anchor        REAL,
    knn           REAL,
    dislike       REAL,
    importance    REAL,
    flags         TEXT,
    cluster_id    TEXT,
    cluster_sources INTEGER,
    llm_relevance INTEGER,
    llm_importance INTEGER,
    llm_topic     TEXT,
    llm_flags     TEXT,
    bootstrap_at  TEXT,
    label         INTEGER,
    labeled_at    TEXT,
    seen_at       TEXT,
    classified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_label ON articles(label);
CREATE INDEX IF NOT EXISTS idx_articles_topic ON articles(topic);
CREATE INDEX IF NOT EXISTS idx_articles_score ON articles(score);
CREATE TABLE IF NOT EXISTS domains (
    name   TEXT PRIMARY KEY,
    vector BLOB,
    count  INTEGER
);
CREATE TABLE IF NOT EXISTS source_stats (
    source          TEXT PRIMARY KEY,
    n               INTEGER,
    mean_relevance  REAL,
    mean_importance REAL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
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
    _migrate(con)
    con.commit()


def _migrate(con):
    columns = {row["name"] for row in con.execute("PRAGMA table_info(articles)")}
    for column, ddl in (
        ("content", "ALTER TABLE articles ADD COLUMN content TEXT"),
        ("fulltext_at", "ALTER TABLE articles ADD COLUMN fulltext_at TEXT"),
        ("dislike", "ALTER TABLE articles ADD COLUMN dislike REAL"),
        ("seen_at", "ALTER TABLE articles ADD COLUMN seen_at TEXT"),
        ("importance", "ALTER TABLE articles ADD COLUMN importance REAL"),
        ("flags", "ALTER TABLE articles ADD COLUMN flags TEXT"),
        ("cluster_id", "ALTER TABLE articles ADD COLUMN cluster_id TEXT"),
        ("cluster_sources", "ALTER TABLE articles ADD COLUMN cluster_sources INTEGER"),
        ("llm_relevance", "ALTER TABLE articles ADD COLUMN llm_relevance INTEGER"),
        ("llm_importance", "ALTER TABLE articles ADD COLUMN llm_importance INTEGER"),
        ("llm_topic", "ALTER TABLE articles ADD COLUMN llm_topic TEXT"),
        ("llm_flags", "ALTER TABLE articles ADD COLUMN llm_flags TEXT"),
        ("bootstrap_at", "ALTER TABLE articles ADD COLUMN bootstrap_at TEXT"),
    ):
        if column not in columns:
            con.execute(ddl)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_importance ON articles(importance)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_cluster ON articles(cluster_id)"
    )


def vector_to_blob(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def blob_to_vector(blob):
    return np.frombuffer(blob, dtype=np.float32)


def upsert_article(con, art, vector=None):
    """Inserta si el guid es nuevo. Devuelve True si se inserto."""
    blob = vector_to_blob(vector) if vector is not None else None
    cur = con.execute(
        """INSERT OR IGNORE INTO articles
           (guid, source, title, link, summary, published, ts, fetched_at, text, content, vector)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            art.get("content"),
            blob,
        ),
    )
    inserted = cur.rowcount > 0
    if not inserted:
        # rellena el contenido de articulos ya existentes que no lo tenian
        con.execute(
            "UPDATE articles SET content = ? WHERE guid = ? AND content IS NULL AND ? IS NOT NULL",
            (art.get("content"), art["guid"], art.get("content")),
        )
    con.commit()
    return inserted


def articles_needing_fulltext(con, min_chars, limit=200):
    return [
        dict(row)
        for row in con.execute(
            """SELECT id, link, content FROM articles
               WHERE fulltext_at IS NULL AND link != ''
                 AND (content IS NULL OR length(content) < ?)
               ORDER BY ts DESC LIMIT ?""",
            (min_chars, limit),
        )
    ]


def set_fulltext(con, article_id, content):
    con.execute(
        "UPDATE articles SET content = COALESCE(?, content), fulltext_at = ? WHERE id = ?",
        (content, now(), article_id),
    )
    con.commit()


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


def articles_for_classification(con, only_unclassified=True):
    sql = """SELECT id, source, ts, cluster_sources, vector, llm_topic
             FROM articles WHERE vector IS NOT NULL"""
    if only_unclassified:
        sql += " AND classified_at IS NULL"
    return [dict(row) for row in con.execute(sql)]


def recent_for_importance(con, since_ts):
    return [
        dict(row)
        for row in con.execute(
            """SELECT id, source, ts, cluster_sources FROM articles
               WHERE vector IS NOT NULL AND ts >= ?""",
            (since_ts,),
        )
    ]


def set_classification(con, article_id, topic, score, anchor, knn, dislike=None,
                       importance=None, flags=None, cluster_id=None,
                       cluster_sources=None):
    con.execute(
        """UPDATE articles
           SET topic = ?, score = ?, anchor = ?, knn = ?, dislike = ?,
               importance = ?, flags = ?, cluster_id = ?, cluster_sources = ?,
               classified_at = ?
           WHERE id = ?""",
        (topic, score, anchor, knn, dislike, importance, flags, cluster_id,
         cluster_sources, now(), article_id),
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


def set_label(con, article_id, label):
    con.execute(
        "UPDATE articles SET label = ?, labeled_at = ? WHERE id = ?",
        (label, now(), article_id),
    )
    con.commit()


def feed_candidates(con, min_score, min_importance, limit=3000):
    return [
        dict(row)
        for row in con.execute(
            """SELECT id, guid, source, title, link, summary, content, ts, topic,
                      score, importance, flags, cluster_id, cluster_sources, label
               FROM articles
               WHERE vector IS NOT NULL AND classified_at IS NOT NULL
                 AND (score >= ? OR importance >= ?)
               ORDER BY ts DESC LIMIT ?""",
            (min_score, min_importance, limit),
        )
    ]


def local_candidates(con, sources, limit=300):
    if not sources:
        return []
    placeholders = ",".join("?" for _ in sources)
    rows = con.execute(
        f"""SELECT id, guid, source, title, link, summary, content, ts, topic,
                   score, importance, flags, cluster_id, cluster_sources, label,
                   llm_relevance, llm_flags, dislike
            FROM articles
            WHERE vector IS NOT NULL AND classified_at IS NOT NULL
              AND source IN ({placeholders})
            ORDER BY ts DESC LIMIT ?""",
        (*sources, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def set_importance(con, article_id, importance, cluster_id, cluster_sources):
    con.execute(
        "UPDATE articles SET importance = ?, cluster_id = ?, cluster_sources = ? WHERE id = ?",
        (importance, cluster_id, cluster_sources, article_id),
    )


def recent_vectors(con, since_ts):
    rows = con.execute(
        """SELECT id, source, ts, vector FROM articles
           WHERE vector IS NOT NULL AND ts >= ? ORDER BY ts DESC""",
        (since_ts,),
    ).fetchall()
    return [dict(row) for row in rows]


def upsert_domain(con, name, vector, count):
    con.execute(
        "INSERT INTO domains(name, vector, count) VALUES(?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET vector=excluded.vector, count=excluded.count",
        (name, vector_to_blob(vector), count),
    )
    con.commit()


def load_domains(con):
    return {
        row["name"]: blob_to_vector(row["vector"])
        for row in con.execute("SELECT name, vector FROM domains WHERE vector IS NOT NULL")
    }


def upsert_source_stat(con, source, n, mean_relevance, mean_importance):
    con.execute(
        "INSERT INTO source_stats(source, n, mean_relevance, mean_importance) VALUES(?,?,?,?) "
        "ON CONFLICT(source) DO UPDATE SET n=excluded.n, "
        "mean_relevance=excluded.mean_relevance, mean_importance=excluded.mean_importance",
        (source, n, mean_relevance, mean_importance),
    )


def load_source_importance(con):
    return {
        row["source"]: row["mean_importance"] or 0.0
        for row in con.execute("SELECT source, mean_importance FROM source_stats")
    }


def set_meta(con, key, value):
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    con.commit()


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def prune_content(con, days=60):
    """Borra el HTML completo de articulos viejos; conserva resumen/vector."""
    cutoff = int(time.time() - days * 86400)
    cur = con.execute(
        "UPDATE articles SET content=NULL WHERE ts < ? AND content IS NOT NULL",
        (cutoff,),
    )
    con.commit()
    return cur.rowcount


def maybe_vacuum(con, days=7):
    last = get_meta(con, "last_vacuum")
    if last:
        try:
            if time.time() - float(last) < days * 86400:
                return False
        except ValueError:
            pass
    try:
        con.commit()
        con.execute("VACUUM")
        set_meta(con, "last_vacuum", str(int(time.time())))
        return True
    except sqlite3.OperationalError:
        return False


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
