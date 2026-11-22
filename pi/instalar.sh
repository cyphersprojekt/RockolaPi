#!/usr/bin/env bash
# Instala la rockola en una Raspberry Pi con Raspberry Pi OS Lite (64 bits).
#
#   ./pi/instalar.sh            servidor + TUI a pantalla completa al prender
#   ./pi/instalar.sh --sin-tui  sólo el servidor (la Pi sin pantalla)
#
# Se puede correr más de una vez sin romper nada. Guía completa: docs/raspberry.md
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
USUARIO="$(id -un)"
CON_TUI=1
[ "${1:-}" = "--sin-tui" ] && CON_TUI=0

if [ "$USUARIO" = "root" ]; then
  echo "Corrélo con tu usuario normal (no con sudo); el script pide sudo cuando lo necesita."
  exit 1
fi

echo "==> 1/5 Paquetes del sistema"
PAQUETES=(mpv python3-venv pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth bluez
          python3-gpiozero python3-lgpio)
[ "$CON_TUI" = 1 ] && PAQUETES+=(cage foot fonts-dejavu-core fonts-noto-color-emoji)
sudo apt-get update -q
sudo apt-get install -y -q "${PAQUETES[@]}"

echo "==> 2/5 Python y dependencias (la primera vez tarda varios minutos)"
"$DIR/iniciar.sh" --preparar

echo "==> 3/5 Audio (PipeWire) funcionando desde que prende, sin que nadie inicie sesión"
sudo loginctl enable-linger "$USUARIO"
# WirePlumber, por defecto, sólo habilita el Bluetooth si hay alguien logueado en la
# pantalla. Lo desactivamos (un archivo para cada versión: 0.5 en Trixie, 0.4 en Bookworm).
mkdir -p ~/.config/wireplumber/wireplumber.conf.d ~/.config/wireplumber/bluetooth.lua.d
cat > ~/.config/wireplumber/wireplumber.conf.d/80-rockola.conf <<'EOF'
# Rockola: Bluetooth aunque no haya nadie logueado (WirePlumber 0.5 o más nuevo)
wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
EOF
cat > ~/.config/wireplumber/bluetooth.lua.d/80-rockola.lua <<'EOF'
-- Rockola: Bluetooth aunque no haya nadie logueado (WirePlumber 0.4)
bluez_monitor.properties["with-logind"] = false
EOF
systemctl --user daemon-reload
systemctl --user enable --now pipewire.socket pipewire-pulse.socket wireplumber.service
systemctl --user restart wireplumber.service || true

echo "==> 4/5 Servicio de la rockola (arranca solo y se reinicia si se cae)"
# Versiones anteriores lo instalaban como servicio del sistema: sacarlo.
if [ -f /etc/systemd/system/rockola.service ]; then
  sudo systemctl disable --now rockola.service || true
  sudo rm -f /etc/systemd/system/rockola.service
  sudo systemctl daemon-reload
fi
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/rockola.service <<EOF
[Unit]
Description=Rockola de oficina
Wants=pipewire.service wireplumber.service
After=pipewire.service wireplumber.service

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/iniciar.sh --modo mpv
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
# Botones físicos (GPIO). Usa el Python del sistema, que trae gpiozero.
cat > ~/.config/systemd/user/rockola-controles.service <<EOF
[Unit]
Description=Rockola: botones físicos (GPIO)
After=rockola.service

[Service]
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/controles.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
# Reinicio diario a las 6 de la mañana: así se actualiza yt-dlp (lo que más se rompe).
cat > ~/.config/systemd/user/rockola-reinicio.service <<'EOF'
[Unit]
Description=Reiniciar la rockola (y actualizar yt-dlp)

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl --user restart rockola.service
EOF
cat > ~/.config/systemd/user/rockola-reinicio.timer <<'EOF'
[Unit]
Description=Reinicio diario de la rockola

[Timer]
OnCalendar=*-*-* 06:00:00

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable rockola.service rockola-controles.service rockola-reinicio.timer
systemctl --user restart rockola.service rockola-controles.service
systemctl --user start rockola-reinicio.timer

if [ "$CON_TUI" = 1 ]; then
  echo "==> 5/5 Pantalla: login automático y TUI a pantalla completa"
  if command -v raspi-config >/dev/null; then
    sudo raspi-config nonint do_boot_behaviour B2   # consola con login automático
  else
    echo "   (no encontré raspi-config: configurá el login automático en tty1 a mano)"
  fi
  # El control por HDMI-CEC (el dispositivo «vc4-hdmi») dice tener un puntero (REL_X/REL_Y): cage lo toma por
  # un mouse y dibuja el cursor en la pantalla. Se le pide a libinput que lo ignore (no se usa para nada).
  REGLA=/etc/udev/rules.d/90-rockola-sin-cursor.rules
  if [ ! -f "$REGLA" ]; then
    echo 'ACTION=="add|change", SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="vc4-hdmi", ENV{LIBINPUT_IGNORE_DEVICE}="1"' \
      | sudo tee "$REGLA" >/dev/null && sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=input --action=change \
      || echo "   (no pude escribir $REGLA: si se ve el cursor del mouse en la pantalla, mirá docs/raspberry.md)"
  fi
  MARCA="# rockola: TUI a pantalla completa"
  if ! grep -qF "$MARCA" ~/.bash_profile 2>/dev/null; then
    cat >> ~/.bash_profile <<EOF

$MARCA
if [ "\$(tty)" = "/dev/tty1" ]; then
  # cage: muestra un solo programa a pantalla completa; foot: la terminal. WLR_LIBINPUT_NO_DEVICES=1: que arranque
  # aunque no haya ningún teclado ni mouse conectado (sin eso, cage se cierra y la pantalla queda en negro).
  # Letra: cambiá size=14. Colores: agregá --tema nord (o tokyo-night, dracula…) al final.
  exec env WLR_LIBINPUT_NO_DEVICES=1 cage -s -- foot --font=monospace:size=14 "$DIR/tui.sh"
fi
EOF
  fi
else
  echo "==> 5/5 Pantalla: no (--sin-tui)"
fi

IP="$(hostname -I | awk '{print $1}')"
PIN="$(sed -n 's/^pin *= *"\(.*\)"/\1/p' "$DIR/rockola.toml")"
echo
echo "Listo."
echo "  Compañeros:        http://$IP:8000/"
echo "  PIN de control:    $PIN   (se cambia en $DIR/rockola.toml)"
echo "  Salida de audio:   ./pi/audio.sh"
echo "  Parlante Bluetooth: ./pi/bluetooth.sh buscar"
echo "  Probar botones:    systemctl --user stop rockola-controles; python3 controles.py --probar"
echo "  Logs:              journalctl --user -u rockola -f"
[ "$CON_TUI" = 1 ] && echo "  Reiniciá la Pi (sudo reboot) para ver la TUI en la pantalla."
exit 0
