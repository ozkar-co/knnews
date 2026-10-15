#!/usr/bin/env bash
# knnews · punto de entrada. Crea el venv, instala dependencias y arranca el
# servicio: servidor en el puerto 3010 y actualizacion cada 2 horas.
#   ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR="${KNNEWS_VENV:-.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d "$VENV_DIR" ]; then
  echo "creando entorno virtual en $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

if [ ! -f "$VENV_DIR/.deps-ok" ] || [ requirements.txt -nt "$VENV_DIR/.deps-ok" ]; then
  echo "instalando dependencias"
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
  touch "$VENV_DIR/.deps-ok"
fi

if [ "$#" -eq 0 ]; then
  set -- serve
fi
exec python -m knnews "$@"
