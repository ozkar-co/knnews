"""Configuracion central de knnews.

Las constantes se pueden sobreescribir por variables de entorno. El archivo
`interests.yaml` (editable a mano) define los temas de interes y los parametros
de clasificacion; `settings()` lo carga y lo cachea.
"""

import os
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("KNNEWS_DATA", BASE_DIR / "data"))
FEEDS_DIR = DATA_DIR / "feeds"
DB_PATH = Path(os.environ.get("KNNEWS_DB", DATA_DIR / "knnews.sqlite3"))
PROFILE_PATH = DATA_DIR / "profile_vectors.json"
MANIFEST_PATH = DATA_DIR / "manifest.json"
URLS_FILE = Path(os.environ.get("KNNEWS_URLS", BASE_DIR / "urls"))
INTERESTS_FILE = Path(
    os.environ.get("KNNEWS_INTERESTS", BASE_DIR / "interests.yaml")
)

HOST = os.environ.get("KNNEWS_HOST", "0.0.0.0")
_port_env = os.environ.get("KNNEWS_PORT", "3010")

EMBED_URL = os.environ.get("KNNEWS_EMBED_URL", "https://embed.ozkr.net/api/embed")
EMBED_BATCH = 32
EMBED_MAX_CHARS = 8000
EMBED_TIMEOUT = int(os.environ.get("KNNEWS_EMBED_TIMEOUT", "60"))

USER_AGENT = os.environ.get("KNNEWS_UA", "knnews/0.1 (+newsboat)")
FETCH_TIMEOUT = int(os.environ.get("KNNEWS_FETCH_TIMEOUT", "25"))

DEFAULT_TOPICS = [
    {
        "slug": "seguridad",
        "name": "Seguridad informatica",
        "description": (
            "vulnerabilidades, exploits, malware, ransomware, hacking etico, "
            "ciberseguridad, analisis de ataques, filtraciones de datos, forense"
        ),
    },
    {
        "slug": "linux",
        "name": "Linux / software libre",
        "description": (
            "GNU/Linux, distribuciones, kernel, software libre y open source, "
            "herramientas de linea de comandos, self-hosting"
        ),
    },
    {
        "slug": "privacidad",
        "name": "Privacidad",
        "description": (
            "privacidad digital, cifrado, vigilancia masiva, derechos digitales, "
            "anonimato, control de datos, grandes tecnologicas"
        ),
    },
    {
        "slug": "ia",
        "name": "IA / ML",
        "description": (
            "inteligencia artificial, aprendizaje automatico, modelos de lenguaje, "
            "redes neuronales, investigacion en IA, etica de la IA"
        ),
    },
    {
        "slug": "programacion",
        "name": "Programacion",
        "description": (
            "programacion, desarrollo de software, arquitectura, ingenieria de "
            "software, lenguajes, rendimiento, ensayos tecnicos"
        ),
    },
    {
        "slug": "cultura-digital",
        "name": "Cultura digital",
        "description": (
            "internet, cultura digital, redes sociales, tecnologia y sociedad, "
            "historia de la informatica, plataformas"
        ),
    },
    {
        "slug": "ciencia",
        "name": "Ciencia",
        "description": (
            "divulgacion cientifica, fisica, biologia, astronomia, investigacion, "
            "descubrimientos, medio ambiente"
        ),
    },
    {
        "slug": "colombia",
        "name": "Colombia",
        "description": (
            "politica y sociedad colombiana, conflicto, corrupcion, economia de "
            "Colombia, actualidad nacional"
        ),
    },
    {
        "slug": "internacional",
        "name": "Internacional",
        "description": (
            "geopolitica, relaciones internacionales, conflictos, politica mundial, "
            "America Latina"
        ),
    },
    {
        "slug": "economia",
        "name": "Economia",
        "description": (
            "economia, mercados, macroeconomia, crisis financiera, desigualdad, "
            "trabajo"
        ),
    },
    {
        "slug": "ensayos",
        "name": "Ensayos / pensamiento",
        "description": (
            "ensayos largos, pensamiento, filosofia, blogs personales, opinion "
            "profunda, ideas"
        ),
    },
    {
        "slug": "videojuegos",
        "name": "Videojuegos",
        "description": "videojuegos, industria del videojuego, analisis de juegos, consolas, PC gaming",
    },
    {
        "slug": "autosostenibilidad",
        "name": "Autosostenibilidad / libertad",
        "description": (
            "autosostenibilidad, vida offgrid, permacultura, libertad individual, "
            "autogobernanza, soberania, tecnologia apropiada, energias renovables"
        ),
    },
]

DEFAULTS = {
    "host": HOST,
    "port": int(_port_env),
    "cycle_hours": 2,
    "threshold": 0.35,
    "knn_k": 15,
    "topic_floor": 0.12,
    "weight_knn": 0.65,
    "weight_anchor": 0.35,
    "weight_dislike": 0.5,
    "feeds_limit": 80,
    "label_limit": 25,
    "base_url": "",
    "fulltext": True,
    "fulltext_min_chars": 900,
    "fulltext_timeout": 20,
    "fulltext_max_chars": 300000,
    "dislikes": [],
    "topics": DEFAULT_TOPICS,
}

_SETTINGS = None


def _topic_slug(topic):
    return topic.get("slug") or topic["name"].lower().replace(" ", "-")


def _normalize(settings):
    topics = settings.get("topics") or DEFAULT_TOPICS
    for t in topics:
        t["slug"] = _topic_slug(t)
        t.setdefault("name", t["slug"])
        t.setdefault("description", t["name"])
    settings["topics"] = topics
    settings["dislikes"] = [str(d) for d in (settings.get("dislikes") or [])]
    port = int(settings.get("port", 3010))
    settings["port"] = port
    if not settings.get("base_url"):
        settings["base_url"] = f"http://localhost:{port}"
    return settings


def load_settings():
    settings = dict(DEFAULTS)
    if INTERESTS_FILE.exists():
        try:
            data = yaml.safe_load(INTERESTS_FILE.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:  # pragma: no cover - mensaje claro
            raise SystemExit(f"interests.yaml invalido: {exc}")
        settings.update({k: v for k, v in data.items() if v is not None})
    if os.environ.get("KNNEWS_PORT"):
        settings["port"] = int(os.environ["KNNEWS_PORT"])
    return _normalize(settings)


def settings():
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = load_settings()
    return _SETTINGS
