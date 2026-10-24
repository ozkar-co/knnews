"""Ciclo completo, planificador y lock para evitar solapamientos."""

import fcntl
import threading
import time
import traceback

from . import classify, cluster, config, feeds, ingest, store

LOCK_PATH = config.DATA_DIR / "cycle.lock"


def run_cycle(log=print):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("ya hay un ciclo en curso; se omite este")
        lock_file.close()
        return {"omitido": True}

    try:
        log("== ciclo knnews ==")
        con = store.connect()
        store.init(con)
        settings = config.settings()
        try:
            new = ingest.ingest_all(con, log=log)
            embedded = ingest.embed_pending(con, log=log)
            enriched = ingest.enrich_fulltext(con, log=log)
            cluster.assign_clusters(con, settings, log=log)
            classified = classify.classify_all(
                con, settings, only_unclassified=True, log=log
            )
            classify.update_importance(con, settings, log=log)
            pruned = store.prune_content(con, days=60)
            if pruned:
                log(f"contenido antiguo depurado: {pruned} articulos")
            store.maybe_vacuum(con, days=7)
            manifest = feeds.publish(con, settings, log=log)
        finally:
            con.close()
        return {
            "nuevos": new,
            "vectorizados": embedded,
            "con_texto": enriched,
            "clasificados": classified,
            "feeds": len(manifest),
        }
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def start_scheduler(log=print):
    settings = config.settings()
    hours = float(settings.get("cycle_hours", 2))

    def loop():
        while True:
            try:
                run_cycle(log=log)
            except Exception:
                log("ciclo fallo:\n" + traceback.format_exc())
            time.sleep(hours * 3600)

    thread = threading.Thread(target=loop, name="knnews-scheduler", daemon=True)
    thread.start()
    return thread
