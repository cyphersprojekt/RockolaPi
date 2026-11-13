#!/usr/bin/env bash
# Parlante Bluetooth para la rockola.
#
#   ./pi/bluetooth.sh buscar               busca parlantes cerca (ponelo antes en modo emparejar)
#   ./pi/bluetooth.sh conectar AA:BB:..    lo empareja y lo deja como el parlante de la rockola:
#                                          se reconecta solo cada vez que lo prendas
#   ./pi/bluetooth.sh estado               si está conectado y por dónde sale el audio
#   ./pi/bluetooth.sh olvidar              dejar de usar el parlante Bluetooth
#   ./pi/bluetooth.sh vigilar              (lo usa el servicio) reconecta si se cayó
set -uo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
AQUI="$DIR/pi"
ARCHIVO="$HOME/.config/rockola/parlante-bluetooth"   # MAC del parlante elegido

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
    echo "Emparejando con $MAC (unos 30 segundos)…"
    # Una sola sesión de bluetoothctl, con un "agente" que acepta solo (los parlantes no piden PIN).
    {
      echo "agent NoInputNoOutput"; echo "default-agent"
      echo "scan on"; sleep 10; echo "scan off"
      echo "pair $MAC"; sleep 10
      echo "trust $MAC"; sleep 1
      echo "connect $MAC"; sleep 8
      echo "quit"
    } | bluetoothctl >/dev/null 2>&1
    if conectado "$MAC"; then
      mkdir -p "$(dirname "$ARCHIVO")"
      echo "$MAC" > "$ARCHIVO"
      usar_como_salida "$MAC" || echo "  (No pude elegirlo como salida; probá ./pi/audio.sh)"
      instalar_servicio
      echo "Listo: conectado a «$(nombre "$MAC")». Se va a reconectar solo cada vez que lo prendas."
    else
      echo "No se pudo conectar. ¿Está prendido, cerca, en modo emparejar y sin otro celular conectado?"
      bluetoothctl info "$MAC" 2>/dev/null | grep -E "Name|Paired|Trusted|Connected" | sed 's/^/  /'
      exit 1
    fi
    ;;

  estado)
    if [ -s "$ARCHIVO" ]; then
      MAC="$(cat "$ARCHIVO")"
      if conectado "$MAC"; then e="conectado"; else e="desconectado (¿apagado?)"; fi
      echo "Parlante: «$(nombre "$MAC")» $MAC — $e"
    else
      echo "No hay parlante Bluetooth configurado."
    fi
    echo
    "$AQUI/audio.sh" listar
    ;;

  olvidar)
    if [ -s "$ARCHIVO" ]; then
      bluetoothctl remove "$(cat "$ARCHIVO")" >/dev/null 2>&1 || true
      rm -f "$ARCHIVO"
    fi
    systemctl --user disable --now rockola-bt.service 2>/dev/null || true
    echo "Listo: la rockola ya no usa el parlante Bluetooth."
    ;;

  vigilar)
    while true; do
      if [ -s "$ARCHIVO" ]; then
        MAC="$(cat "$ARCHIVO")"
        if ! conectado "$MAC"; then
          bluetoothctl connect "$MAC" >/dev/null 2>&1 && echo "Reconectado a $MAC"
        fi
      fi
      sleep 30
    done
    ;;

  *)
    sed -n '2,10p' "$0"
    exit 1
    ;;
esac
