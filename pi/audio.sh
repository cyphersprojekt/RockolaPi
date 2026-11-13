#!/usr/bin/env bash
# Salidas de audio de la Raspberry.
#
#   ./pi/audio.sh               lista las salidas (* = la que se usa)
#   ./pi/audio.sh usar 2        manda el sonido a la salida número 2 de la lista
#   ./pi/audio.sh volumen 100   volumen general de la salida (0-150, en %)
#   ./pi/audio.sh probar        suena un tono de prueba de 2 segundos
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
salidas() { pw-dump | python3 "$AQUI/salidas.py" "$@"; }

case "${1:-listar}" in
  listar)
    echo "Salidas de audio (* = la que se usa):"
    salidas listar
    echo
    echo "Para cambiar: ./pi/audio.sh usar <número>"
    ;;
  usar)
    [[ "${2:-}" =~ ^[0-9]+$ ]] || { echo "Uso: $0 usar <número de la lista>"; exit 1; }
    ID="$(salidas id "$2")"
    [ -n "$ID" ] || { echo "No hay una salida número $2."; exit 1; }
    wpctl set-default "$ID"   # WirePlumber lo recuerda aunque se reinicie la Pi
    salidas listar
    ;;
  volumen)
    [[ "${2:-}" =~ ^[0-9]+$ ]] || { echo "Uso: $0 volumen <0-150>"; exit 1; }
    wpctl set-volume -l 1.5 @DEFAULT_AUDIO_SINK@ "$2%"
    echo "Volumen de la salida: $2%  (el de la rockola se maneja aparte, desde la web)"
    ;;
  probar)
    WAV="$(mktemp --suffix=.wav)"
    python3 - "$WAV" <<'PY'
import math, struct, sys, wave
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
    w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 44100))) for i in range(88200)))
PY
    echo "Tono de prueba (2 segundos)…"
    pw-play "$WAV" || echo "No pude reproducir. ¿Está corriendo PipeWire? (systemctl --user status pipewire)"
    rm -f "$WAV"
    ;;
  *)
    sed -n '2,8p' "$0"
    exit 1
    ;;
esac
