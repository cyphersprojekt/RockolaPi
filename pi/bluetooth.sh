#!/usr/bin/env bash
# Parlante Bluetooth para la rockola.
#
#   ./pi/bluetooth.sh buscar               busca parlantes cerca (ponelo antes en modo emparejar)
#   ./pi/bluetooth.sh conectar AA:BB:..    lo empareja y lo deja como el parlante de la rockola:
#                                          se reconecta solo cada vez que lo prendas
#   ./pi/bluetooth.sh estado               si está conectado y por dónde sale el audio
#   ./pi/bluetooth.sh agregar AA:BB:..     suma otro parlante (suenan todos a la vez, ver «juntar»)
#   ./pi/bluetooth.sh juntar               la música sale por todos los parlantes a la vez
#   ./pi/bluetooth.sh separar              vuelve a sonar sólo por el primer parlante
#   ./pi/bluetooth.sh olvidar              dejar de usar los parlantes Bluetooth
#   ./pi/bluetooth.sh vigilar              (lo usa el servicio) reconecta si se cayó
set -uo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
AQUI="$DIR/pi"
ARCHIVO="$HOME/.config/rockola/parlante-bluetooth"   # MAC del parlante elegido
EXTRAS="$HOME/.config/rockola/parlantes-extra"       # MAC de los demás parlantes (una por línea)
COMBINADO="$HOME/.config/pipewire/pipewire.conf.d/90-rockola-todos.conf"
NODO_TODOS="todos_los_parlantes"

preparar() {
  sudo rfkill unblock bluetooth 2>/dev/null || true
  bluetoothctl power on >/dev/null 2>&1
}
conectado() { bluetoothctl info "$1" 2>/dev/null | grep -q "Connected: yes"; }
nombre() { bluetoothctl info "$1" 2>/dev/null | sed -n 's/^\s*Name: //p'; }
usar_como_salida() {
  # Que el audio salga por el parlante. WirePlumber lo recuerda: si el parlante se apaga,
  # el sonido pasa a otra salida, y cuando vuelve, vuelve a salir por él.
  local id=""
  for _ in $(seq 10); do
    id="$(pw-dump | python3 "$AQUI/salidas.py" bt "$1")"
    [ -n "$id" ] && break
    sleep 1
  done
  [ -n "$id" ] && wpctl set-default "$id"
}
emparejar() {
  echo "Emparejando con $1 (unos 30 segundos)…"
  # Una sola sesión de bluetoothctl, con un "agente" que acepta solo (los parlantes no piden PIN).
  {
    echo "agent NoInputNoOutput"; echo "default-agent"
    echo "scan on"; sleep 10; echo "scan off"
    echo "pair $1"; sleep 10
    echo "trust $1"; sleep 1
    echo "connect $1"; sleep 8
    echo "quit"
  } | bluetoothctl >/dev/null 2>&1
  conectado "$1"
}
fallo_emparejar() {
  echo "No se pudo conectar. ¿Está prendido, cerca, en modo emparejar y sin otro celular conectado?"
  bluetoothctl info "$1" 2>/dev/null | grep -E "Name|Paired|Trusted|Connected" | sed 's/^/  /'
}
macs() { cat "$ARCHIVO" "$EXTRAS" 2>/dev/null | grep -E '^([0-9A-F]{2}:){5}[0-9A-F]{2}$' | sort -u; }
reiniciar_audio() {
  # Se reinicia PipeWire para que lea la configuración; la rockola (mpv) vuelve a abrir la salida.
  systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service
  sleep 3
  systemctl --user restart rockola.service 2>/dev/null || true
}
instalar_servicio() {
  mkdir -p ~/.config/systemd/user
  cat > ~/.config/systemd/user/rockola-bt.service <<EOT
[Unit]
Description=Rockola: reconectar el parlante Bluetooth
After=bluetooth.target

[Service]
ExecStart=$AQUI/bluetooth.sh vigilar
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOT
  systemctl --user daemon-reload
  systemctl --user enable --now rockola-bt.service
  systemctl --user restart rockola-bt.service
}

case "${1:-}" in
  buscar)
    preparar
    echo "Buscando durante 20 segundos… (el parlante tiene que estar en modo emparejar)"
    bluetoothctl --timeout 20 scan on >/dev/null 2>&1
    echo
    echo "Encontrados (MAC y nombre):"
    bluetoothctl devices | sed 's/^Device /  /'
    echo
    echo "Para conectar uno:  ./pi/bluetooth.sh conectar <MAC>"
    ;;

  conectar)
    MAC="${2:-}"
    [[ "$MAC" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]] || { echo "Uso: $0 conectar AA:BB:CC:DD:EE:FF"; exit 1; }
    MAC="${MAC^^}"
    preparar
    if emparejar "$MAC"; then
      mkdir -p "$(dirname "$ARCHIVO")"
      echo "$MAC" > "$ARCHIVO"
      usar_como_salida "$MAC" || echo "  (No pude elegirlo como salida; probá ./pi/audio.sh)"
      instalar_servicio
      echo "Listo: conectado a «$(nombre "$MAC")». Se va a reconectar solo cada vez que lo prendas."
      echo "Para sumar otro parlante que suene a la vez:  ./pi/bluetooth.sh agregar <MAC>  y después  ./pi/bluetooth.sh juntar"
    else
      fallo_emparejar "$MAC"
      exit 1
    fi
    ;;

  agregar)
    MAC="${2:-}"
    [[ "$MAC" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]] || { echo "Uso: $0 agregar AA:BB:CC:DD:EE:FF"; exit 1; }
    MAC="${MAC^^}"
    [ -s "$ARCHIVO" ] || { echo "Primero configurá el parlante principal:  $0 conectar <MAC>"; exit 1; }
    [ "$MAC" = "$(cat "$ARCHIVO")" ] && { echo "Ése ya es el parlante principal."; exit 1; }
    preparar
    if emparejar "$MAC"; then
      mkdir -p "$(dirname "$EXTRAS")"
      grep -qxF "$MAC" "$EXTRAS" 2>/dev/null || echo "$MAC" >> "$EXTRAS"
      instalar_servicio
      echo "Listo: «$(nombre "$MAC")» agregado. Para que suenen todos a la vez:  $0 juntar"
    else
      fallo_emparejar "$MAC"
      exit 1
    fi
    ;;

  juntar)
    # Una salida virtual que reparte el audio a todos los parlantes Bluetooth conectados
    # (módulo combine-stream de PipeWire). Los que se conecten después se suman solos.
    mkdir -p "$(dirname "$COMBINADO")"
    cat > "$COMBINADO" <<EOT
# Rockola: una salida que suena en todos los parlantes Bluetooth a la vez
context.modules = [
  { name = libpipewire-module-combine-stream
    args = {
      combine.mode = sink
      node.name = "$NODO_TODOS"
      node.description = "Todos los parlantes"
      combine.props = { audio.position = [ FL FR ] }
      stream.props = { stream.dont-remix = true }
      stream.rules = [
        { matches = [ { media.class = "Audio/Sink", node.name = "~bluez_output.*" } ]
          actions = { create-stream = { } } }
      ]
    }
  }
]
EOT
    reiniciar_audio
    id=""
    for _ in $(seq 15); do
      id="$(pw-dump | python3 "$AQUI/salidas.py" nodo "$NODO_TODOS")"
      [ -n "$id" ] && break
      sleep 1
    done
    if [ -n "$id" ] && wpctl set-default "$id"; then
      echo "Listo: la música sale por todos los parlantes Bluetooth conectados."
      echo "(Si uno suena atrasado respecto del otro, es la latencia propia de cada parlante.)"
    else
      echo "No apareció la salida «Todos los parlantes». Mirá: journalctl --user -u pipewire -n 30"
      exit 1
    fi
    ;;

  separar)
    rm -f "$COMBINADO"
    reiniciar_audio
    if [ -s "$ARCHIVO" ]; then usar_como_salida "$(cat "$ARCHIVO")" || echo "  (Elegí la salida con ./pi/audio.sh)"; fi
    echo "Listo: la música sale sólo por el parlante principal."
    ;;

  estado)
    if [ -s "$ARCHIVO" ]; then
      for MAC in $(macs); do
        if conectado "$MAC"; then e="conectado"; else e="desconectado (¿apagado?)"; fi
        echo "Parlante: «$(nombre "$MAC")» $MAC — $e"
      done
      [ -f "$COMBINADO" ] && echo "Modo: todos a la vez (./pi/bluetooth.sh separar para volver a uno)"
    else
      echo "No hay parlante Bluetooth configurado."
    fi
    echo
    "$AQUI/audio.sh" listar
    ;;

  olvidar)
    for MAC in $(macs); do bluetoothctl remove "$MAC" >/dev/null 2>&1 || true; done
    rm -f "$ARCHIVO" "$EXTRAS"
    systemctl --user disable --now rockola-bt.service 2>/dev/null || true
    if [ -f "$COMBINADO" ]; then rm -f "$COMBINADO"; reiniciar_audio; fi
    echo "Listo: la rockola ya no usa parlantes Bluetooth."
    ;;

  vigilar)
    while true; do
      for MAC in $(macs); do
        if ! conectado "$MAC"; then
          bluetoothctl connect "$MAC" >/dev/null 2>&1 && echo "Reconectado a $MAC"
        fi
      done
      sleep 30
    done
    ;;

  *)
    sed -n '2,14p' "$0"
    exit 1
    ;;
esac
