#!/usr/bin/env bash
# La rockola en la terminal. Opciones: --url http://otra-maquina:8000   --tema nord
#   --ciclo 30 (cada cuántos segundos cambia sola entre el reproductor y las estadísticas; 0 = nunca)
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Primero corré ./iniciar.sh"; exit 1; }
exec .venv/bin/python tui.py "$@"
