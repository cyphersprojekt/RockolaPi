#!/usr/bin/env bash
# Levanta la rockola. La primera vez (o si cambian las dependencias) prepara el entorno.
#
#   ./iniciar.sh                      modo automático (mpv si está instalado)
#   ./iniciar.sh --modo navegador     la música suena en http://localhost:8000/player
#   ./iniciar.sh --ip 192.168.0.20    si el link/QR muestra la IP de otra red
#   ./iniciar.sh --preparar           sólo instalar, no arrancar
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Creando el entorno virtual…"
  python3 -m venv .venv || { echo "Falta python3-venv. Instalalo con: sudo apt install python3-venv"; exit 1; }
fi

# Raspberry Pi OS trae piwheels en /etc/pip.conf. En 64 bits no aporta nada (sólo tiene
# paquetes para ARM de 32 bits; PyPI ya tiene todo) y suele cortar las descargas
# (SSLEOFError, RemoteDisconnected). Lo apagamos sólo para este entorno.
if [ ! -f .venv/pip.conf ] && grep -qs piwheels /etc/pip.conf \
   && .venv/bin/python -c 'import sys; sys.exit(sys.maxsize <= 2**32)'; then
  printf '# Generado por iniciar.sh: sin piwheels (Pi OS de 64 bits)\n[global]\nextra-index-url =\n' > .venv/pip.conf
fi

# pip con paciencia: si la red corta, reintenta en vez de abortar la instalación.
pip_instalar() {
  local intento
  for intento in 1 2 3; do
    .venv/bin/pip install -q --retries 10 --timeout 30 "$@" && return 0
    [ "$intento" -lt 3 ] && { echo "  (Se cortó la descarga; reintento en 10 segundos…)"; sleep 10; }
  done
  return 1
}

if ! cmp -s requirements.txt .venv/.requirements-instalados; then
  echo "Instalando dependencias (puede tardar un par de minutos)…"
  pip_instalar --upgrade pip || echo "  (No pude actualizar pip; sigo con el que hay.)"
  pip_instalar -r requirements.txt || {
    echo "No pude descargar las dependencias: revisá la conexión a internet y volvé a correr esto."
    exit 1
  }
  # Deno: el runtime de JavaScript que yt-dlp usa con YouTube. No hay para ARM de 32 bits;
  # sin él yt-dlp anda igual, con menos formatos.
  pip_instalar --only-binary=:all: deno \
    || echo "  (No pude instalar Deno. Anda igual; en una Raspberry conviene Pi OS de 64 bits.)"
  cp requirements.txt .venv/.requirements-instalados
  touch .venv/.ultima-actualizacion
fi

# yt-dlp se rompe cuando YouTube cambia algo, y lo arreglan rápido: actualizar una vez por día.
if [ -z "$(find .venv/.ultima-actualizacion -mmin -1200 2>/dev/null)" ]; then
  echo "Buscando actualizaciones de yt-dlp y ytmusicapi…"
  .venv/bin/pip install -q --timeout 15 --retries 1 --upgrade "yt-dlp[default]" ytmusicapi \
    && touch .venv/.ultima-actualizacion \
    || echo "  (No pude actualizar; sigo con lo que hay.)"
fi

# Configuración: la primera vez, copiar el ejemplo con un PIN al azar.
if [ ! -f rockola.toml ]; then
  PIN="$(.venv/bin/python -c 'import secrets; print(f"{secrets.randbelow(10**6):06d}")')"
  sed "s/^pin = \"CAMBIAR\"/pin = \"$PIN\"/" rockola.ejemplo.toml > rockola.toml
  echo "Creé rockola.toml. PIN para controlar la música desde la web: $PIN"
fi

[ "$1" = "--preparar" ] && { echo "Listo."; exit 0; }

if ! command -v mpv >/dev/null && [[ " $* " != *" navegador "* ]]; then
  echo "  (mpv no está instalado: arranco en modo navegador. Para que suene en esta máquina: sudo apt install mpv)"
fi

exec .venv/bin/python server.py "$@"
