"""Interfaz de linea de comandos."""

import argparse
import sys

from . import audit, bootstrap, classify, config, feeds, ingest, service, store


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
    classify.classify_all(con, config.settings(), only_unclassified=False)
    feeds.publish(con, config.settings())
    con.close()


def cmd_embed(args):
    con = store.connect()
    store.init(con)
    ingest.embed_pending(con)
    con.close()


def cmd_stats(args):
    con = store.connect()
    store.init(con)
    for key, value in store.stats(con).items():
        print(f"{key:>14}: {value}")
    con.close()


def cmd_check_feeds(args):
    audit.run()


def cmd_bootstrap(args):
    bootstrap.run(
        limit=args.limit,
        workers=args.workers,
        dry_run=args.dry_run,
        force=args.force,
        derive_only=args.derive_only,
    )


def cmd_validate(args):
    con = store.connect()
    store.init(con)
    print(bootstrap.validate(con))
    con.close()


def build_parser():
    parser = argparse.ArgumentParser(prog="knnews", description="Filtro personal de noticias")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="servidor + ciclo cada N horas (por defecto)")
    sub.add_parser("cycle", help="un ciclo completo: leer, vectorizar, clasificar, publicar")
    sub.add_parser("classify", help="recalcular clasificacion y republicar feeds")
    sub.add_parser("embed", help="vectorizar articulos pendientes")
    sub.add_parser("stats", help="estadisticas de la base")
    sub.add_parser("check-feeds", help="audita si cada fuente trae el texto completo")

    boot = sub.add_parser("bootstrap", help="etiquetado offline con LLM (una sola vez)")
    boot.add_argument("--limit", type=int, default=None)
    boot.add_argument("--workers", type=int, default=8)
    boot.add_argument("--dry-run", action="store_true")
    boot.add_argument("--force", action="store_true")
    boot.add_argument("--derive-only", action="store_true")

    sub.add_parser("validate", help="comparar el bootstrap con las etiquetas previas")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"
    handlers = {
        "serve": cmd_serve,
        "cycle": cmd_cycle,
        "classify": cmd_classify,
        "embed": cmd_embed,
        "stats": cmd_stats,
        "check-feeds": cmd_check_feeds,
        "bootstrap": cmd_bootstrap,
        "validate": cmd_validate,
    }
    handlers[command](args)


if __name__ == "__main__":
    main(sys.argv[1:])
