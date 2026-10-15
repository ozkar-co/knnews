"""Ciclo completo y planificador cada N horas."""

import threading
import time
import traceback

from . import classify, config, feeds, ingest, store


def run_cycle(log=print):
    log("== ciclo knnews ==")
    con = store.connect()
    store.init(con)
    settings = config.settings()
    try:
        new = ingest.ingest_all(con, log=log)
        embedded = ingest.embed_pending(con, log=log)
        enriched = ingest.enrich_fulltext(con, log=log)
        classify.classify_all(con, settings, only_unclassified=True, log=log)
        manifest = feeds.publish(con, settings, log=log)
    finally:
        con.close()
    return {"nuevos": new, "vectorizados": embedded, "con_texto": enriched,
            "feeds": len(manifest)}


def start_scheduler(log=print):
    settings = config.settings()
    hours = float(settings.get("cycle_hours", 2))

    def loop():
        while True:
            try:
                run_cycle(log=log)
            except Exception:  # noqa: BLE001 - el servicio no debe morir
                log("ciclo fallo:\n" + traceback.format_exc())
            time.sleep(hours * 3600)

    thread = threading.Thread(target=loop, name="knnews-scheduler", daemon=True)
    thread.start()
    return thread
