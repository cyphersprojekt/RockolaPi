#!/usr/bin/env bash
# La rockola en la terminal. Opciones: --url http://otra-maquina:8000   --tema nord
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Primero corré ./iniciar.sh"; exit 1; }
exec .venv/bin/python tui.py "$@"
