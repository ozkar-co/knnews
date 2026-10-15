"""Interfaz de linea de comandos."""

import argparse
import sys
import textwrap

from . import classify, config, feeds, ingest, service, store


def cmd_serve(args):
    settings = config.settings()
    con = store.connect()
    store.init(con)
    con.close()
    print(f"knnews · servidor en {settings['host']}:{settings['port']}")
    service.start_scheduler()
    from .server import serve

    serve(settings)


def cmd_cycle(args):
    print(service.run_cycle())


def cmd_classify(args):
    con = store.connect()
    store.init(con)
    settings = config.settings()
    classify.classify_all(con, settings, only_unclassified=False)
    feeds.publish(con, settings)
    con.close()


def cmd_embed(args):
    con = store.connect()
    store.init(con)
    ingest.embed_pending(con)
    con.close()


def cmd_label(args):
    con = store.connect()
    store.init(con)
    limit = args.limit or config.settings().get("label_limit", 25)
    rows = store.unlabeled(con, limit=limit)
    if not rows:
        print("No hay articulos sin etiquetar con vector. Corre './run.sh cycle' primero.")
        con.close()
        return

    print(f"{len(rows)} articulos por etiquetar.")
    print("y = me interesa   n = no me interesa   s = saltar   q = guardar y salir\n")
    count = {"y": 0, "n": 0, "s": 0}
    for index, row in enumerate(rows, 1):
        score = row.get("score")
        score_txt = f"{score:.2f}" if score is not None else "-"
        print("-" * 72)
        print(f"[{index}/{len(rows)}] {row['source']}  ·  tema={row.get('topic') or '-'}  score={score_txt}")
        print(row["title"])
        if row.get("link"):
            print(row["link"])
        summary = row.get("summary") or "(sin resumen)"
        print(textwrap.fill(summary, width=100))
        while True:
            answer = input("\n¿relevante? [y/n/s/q]: ").strip().lower()
            if answer in ("y", "n", "s", "q"):
                break
        if answer == "q":
            break
        if answer == "s":
            count["s"] += 1
            continue
        store.set_label(con, row["id"], 1 if answer == "y" else 0)
        count[answer] += 1

    con.close()
    print(f"\netiquetados: {count['y']} relevantes, {count['n']} descartados, "
          f"{count['s']} saltados")
    print("Siguiente paso: './run.sh classify' para recalcular los feeds.")


def cmd_stats(args):
    con = store.connect()
    store.init(con)
    for key, value in store.stats(con).items():
        print(f"{key:>14}: {value}")
    con.close()


def cmd_feeds(args):
    con = store.connect()
    store.init(con)
    settings = config.settings()
    classify.classify_all(con, settings, only_unclassified=False)
    feeds.publish(con, settings)
    con.close()


def build_parser():
    parser = argparse.ArgumentParser(prog="knnews", description="Filtro personal de noticias")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="arranca el servidor + planificador cada N horas")

    sub.add_parser("cycle", help="un ciclo completo: leer, vectorizar, clasificar, publicar")

    sub.add_parser("classify", help="recalcular clasificacion y republicar feeds")

    sub.add_parser("feeds", help="alias de classify (republica feeds)")

    sub.add_parser("embed", help="vectorizar articulos pendientes")

    label = sub.add_parser("label", help="minientrenamiento interactivo (y/n)")
    label.add_argument("--limit", type=int, default=None, help="cuantos revisar")

    sub.add_parser("stats", help="estadisticas de la base")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"
    handlers = {
        "serve": cmd_serve,
        "cycle": cmd_cycle,
        "classify": cmd_classify,
        "feeds": cmd_feeds,
        "embed": cmd_embed,
        "label": cmd_label,
        "stats": cmd_stats,
    }
    handlers[command](args)


if __name__ == "__main__":
    main(sys.argv[1:])
