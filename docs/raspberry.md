# Rockola en una Raspberry Pi

Al final de esta guía vas a tener una Raspberry que, apenas la enchufás:

- arranca la rockola sola (y se recupera sola si algo falla),
- muestra en su pantalla qué está sonando, la cola y un QR para pedir temas,
- hace sonar la música por un parlante Bluetooth (o por el jack, HDMI o USB),
- y se maneja sin teclado: tus compañeros piden desde el celular y vos controlás
  volumen, pausa y la cola desde la web, con un PIN.

Tiempo estimado: una hora, la mayor parte esperando descargas.

> **Ojo:** los scripts de esta guía (`pi/*.sh`) todavía no se probaron en una Raspberry real.
> Están escritos con cuidado, pero si algo no anda, la sección
> [Si algo falla](#11-si-algo-falla) tiene los comandos para ver qué pasa.

---

## 1. Qué necesitás

| | Recomendado | Notas |
|---|---|---|
| **Raspberry Pi** | **Pi 4 (2 GB o más)** | La Pi 3B+ anda, un poco más lenta para arrancar cada tema. La Pi 5 anda perfecto pero **no tiene jack de audio** (Bluetooth, USB o HDMI sí). La Zero 2 W tiene poca memoria (512 MB): puede andar, pero no la recomiendo. |
| **Fuente** | La oficial de la Pi | Con fuentes truchas la Pi se reinicia sola o el Bluetooth anda mal. |
| **microSD** | 32 GB o más, clase A1/A2 | Una marca conocida (SanDisk, Samsung). Las canciones que suenan quedan guardadas ahí, hasta 16 GB (`guardar_gb` en `rockola.toml`; siempre deja 2 GB libres). Con 16 GB anda, pero entran menos. |
| **Pantalla** | HDMI o la oficial DSI | Cualquier tamaño. En 7" (800×480) entra todo. |
| **Gabinete** | El imprimible de [gabinete.md](gabinete.md) | Hecho para una pantalla de 230 × 150 × 5 mm; las medidas se ajustan. |
| **Parlante** | Bluetooth, o uno potenciado por jack 3,5 mm, o una placa de sonido USB | El jack de la Pi suena "aceptable"; para algo mejor, USB o Bluetooth. |
| **Red** | Cable Ethernet, o wifi de 5 GHz | En la Pi, el wifi de 2,4 GHz y el Bluetooth comparten antena: **si usás parlante Bluetooth y wifi de 2,4 GHz, puede haber cortes**. |
| Para instalar | Una PC en la misma red y un lector de microSD | No hace falta teclado ni mouse en la Pi. |

## 2. Grabar la tarjeta

1. En tu PC, instalá **Raspberry Pi Imager**: <https://www.raspberrypi.com/software/>
   (en Ubuntu: `sudo apt install rpi-imager`).
2. Abrilo y elegí:
   - **Dispositivo:** tu modelo de Raspberry.
   - **Sistema operativo:** *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (64-bit)**.
     - *Lite*: sin escritorio. La rockola dibuja su propia pantalla y así le queda más memoria.
     - *64-bit*: hace falta para Deno, que yt-dlp usa para descargar de YouTube.
   - **Almacenamiento:** la microSD.
3. Cuando pregunte si querés **personalizar la configuración**, decí que sí y completá:
   - **Nombre de la máquina:** `rockola` (después vas a entrar con `rockola.local`).
   - **Usuario y contraseña:** los que quieras (en esta guía: `usuario`).
   - **Wifi:** nombre de la red, contraseña y **país** (por ejemplo `AR` para Argentina). Sin el
     país, el wifi y el Bluetooth quedan bloqueados. Si vas a usar cable, igual poné el país.
   - **Zona horaria:** la tuya (la rockola se reinicia todos los días a las 6 de la mañana).
   - **Servicios:** activá **SSH** (con contraseña alcanza).
4. Grabá y esperá a que verifique.

## 3. Primer arranque

1. Poné la microSD en la Pi, conectá la pantalla (y el cable de red si usás) y enchufala.
   El primer arranque tarda un par de minutos y se reinicia solo una vez.
2. Desde tu PC, entrá por SSH:

   ```bash
   ssh usuario@rockola.local
   ```

   Si `rockola.local` no aparece, buscá la IP de la Pi en el router (o en la pantalla de la Pi)
   y usá `ssh usuario@192.168.x.x`.
3. Actualizá todo (tarda un rato) y reiniciá:

   ```bash
   sudo apt update && sudo apt full-upgrade -y && sudo apt install -y rsync
   sudo reboot
   ```

## 4. Copiar la rockola a la Pi

Desde **tu PC** (no desde la Pi):

```bash
rsync -av --exclude .venv --exclude __pycache__ ~/RockolaPi/ usuario@rockola.local:~/RockolaPi/
```

- `.venv` no se copia porque está armado para tu PC; la Pi arma el suyo.
- Si copiás también `rockola.toml`, la Pi usa tu misma configuración y el mismo PIN. Si no,
  el instalador crea uno nuevo con un PIN al azar.

## 5. Instalar

En la Pi (por SSH):

```bash
cd ~/RockolaPi
./pi/instalar.sh
```

Si la Pi **no va a tener pantalla**, usá `./pi/instalar.sh --sin-tui`.

Tarda entre 5 y 15 minutos. Hace esto:

1. Instala mpv, PipeWire (el sistema de audio), Bluetooth y, para la pantalla, `cage` y `foot`.
2. Arma el entorno de Python con las dependencias (yt-dlp, Deno, Textual…).
3. Deja el audio funcionando desde que la Pi prende, sin que nadie inicie sesión.
4. Instala la rockola como servicio: arranca sola, se reinicia si se cae y todos los días a las
   6:00 (así se actualiza yt-dlp). También deja andando los [botones físicos](botones.md).
5. Configura el login automático y la TUI a pantalla completa.

Al final te muestra el **link para tus compañeros** y el **PIN**. Anotalos y reiniciá:

```bash
sudo reboot
```

Al volver, la pantalla tiene que mostrar la rockola con el QR.

## 6. Elegir por dónde sale el sonido

```bash
./pi/audio.sh             # lista las salidas; la marcada con * es la que se usa
./pi/audio.sh usar 1      # usar la salida número 1 de la lista
./pi/audio.sh probar      # tono de prueba de 2 segundos
./pi/audio.sh volumen 100 # volumen general de la salida
```

Las salidas típicas son "Built-in Audio Stereo" (el jack), "… (HDMI)" (los parlantes de la
pantalla, si tiene) y lo que conectes por USB o Bluetooth. La elección queda guardada.

> El volumen de `audio.sh` es el de la salida. El de la música se maneja desde la web.
> Lo ideal es dejar la salida en 100 % y regular desde la web.

## 7. Parlante Bluetooth

1. Prendé el parlante y ponelo en **modo emparejar**. Suele ser mantener apretado el botón de
   Bluetooth hasta que la luz titila rápido. Si está conectado a un celular, desconectalo.
2. Buscalo:

   ```bash
   ./pi/bluetooth.sh buscar
   ```

   Aparece una lista con la MAC y el nombre, por ejemplo `FC:58:FA:12:34:56 JBL Flip 5`.
3. Conectalo:

   ```bash
   ./pi/bluetooth.sh conectar FC:58:FA:12:34:56
   ```

Y listo. Desde ahora:
- **Se reconecta solo** cada vez que prendas el parlante: la Pi lo intenta cada 30 segundos.
- Si el parlante se apaga, la música sigue sonando por otra salida (el jack, por ejemplo), sin
  saltear canciones. Cuando el parlante vuelve, la música vuelve a salir por él.
- **Al prender la Pi** (o al reiniciar la rockola, si en 10 segundos nadie pidió nada), la rockola
  espera a que se conecte el parlante y arranca a sonar al azar (con las canciones guardadas),
  aunque lo prendas un rato después. Se apaga con `azar_al_prender = false` en `rockola.toml`.

Otros comandos:

```bash
./pi/bluetooth.sh estado   # ¿está conectado? ¿por dónde sale el audio?
./pi/bluetooth.sh olvidar  # dejar de usarlo (por ejemplo, para cambiar de parlante)
```

**Si no conecta:**
- Muchos parlantes sólo aceptan un dispositivo a la vez: apagá el Bluetooth de los celulares
  que lo usaban.
- Acercalo a la Pi para el primer emparejamiento.
- Probalo a mano con `bluetoothctl` (adentro: `scan on`, esperá a que aparezca,
  `pair MAC`, `trust MAC`, `connect MAC`, `quit`) y después corré
  `./pi/bluetooth.sh conectar MAC` igual, para que quede configurado.

## 8. Usarla sin teclado

- **Botones físicos** debajo de la pantalla: volumen −, anterior, play/pausa, siguiente y volumen +.
  Pulsadores táctiles directo al GPIO, sin soldar: ver **[botones.md](botones.md)** (con diagrama).
- **Compañeros:** entran al link (o escanean el QR de la pantalla), ponen su nombre y piden temas.
- **Vos:** en la misma página, tocá **🔒 Controlar** y poné el PIN. Te aparecen:
  - ⏸/▶ pausa, ⏭ siguiente, y el volumen;
  - en cada canción de la cola: ⤒ (que suene a continuación), ↑ (subir un lugar) y ✕ (quitar).

  El celular recuerda el PIN. Para salir, tocá **🔓 Controlando**.
- **Pantalla de la Pi:** sólo muestra. Si algún día le conectás un teclado, las teclas de la TUI
  también funcionan.
- **Otra pantalla (una tele):** `http://<ip-de-la-pi>:8000/pantalla` en cualquier navegador.
- **Con auriculares:** en la página, **📻 radio** (arriba a la derecha) hace sonar en esa compu lo
  mismo que la rockola, a la par. Si prefieren silencio en la oficina, bajen el volumen de la
  rockola a 0 desde la web: la música sigue en los auriculares.
- **Otras salas:** otro equipo puede tener su propia música sin otra Raspberry: en la página,
  **sala: ＋ nueva sala…**. Esa sala no suena en el parlante de la Pi: la escuchan con **📻 radio**,
  en sus compus o en una con parlante.

## 9. Configuración

Todo está en `~/RockolaPi/rockola.toml` (cada opción está explicada adentro):

```bash
nano ~/RockolaPi/rockola.toml
systemctl --user restart rockola    # para que tome los cambios
```

Lo más común:
- `pin`: el PIN de control.
- `max_por_persona`: cuántas canciones pendientes puede tener cada uno (3).
- `duracion_maxima`: en segundos (600 = 10 minutos).
- `volumen`: volumen inicial al arrancar.
- `normalizar = true`: iguala el volumen entre canciones, si unas suenan mucho más fuerte que otras.
- `ip`: si el link o el QR muestran una IP que no es la de la red de la oficina.

**Pantalla:** el tamaño de la letra y los colores se cambian en `~/.bash_profile`, en la línea
de `cage … foot --font=monospace:size=14 …`. Para otros colores, agregá al final
`--tema nord` (o `tokyo-night`, `dracula`, `monokai`, `catppuccin-mocha`). Reiniciá para ver el
cambio.

## 10. Uso diario y mantenimiento

| Para… | Comando (en la Pi) |
|---|---|
| Ver qué está pasando | `journalctl --user -u rockola -f` |
| Reiniciar la rockola | `systemctl --user restart rockola` |
| Apagar la Pi | `sudo poweroff` (desenchufarla de golpe puede dañar la microSD) |
| Ver la IP | `hostname -I` |
| Ver el PIN | `grep pin ~/RockolaPi/rockola.toml` |

**Actualizar la rockola** cuando cambies algo en tu PC: repetí el `rsync` del paso 4 y después
`systemctl --user restart rockola`. Si cambiaron las dependencias, se instalan solas al reiniciar.
Las colas y los chats se vacían; si en 10 segundos nadie pide nada, la principal arranca sola al
azar (`azar_al_prender`), así no queda en silencio.

**Reinicio diario:** todos los días a las 6:00 la rockola se reinicia y actualiza yt-dlp. Las colas
y los chats se vacían, y no arranca a sonar sola. Para cambiar la hora:
`~/.config/systemd/user/rockola-reinicio.timer`.

**Más lugar para las canciones:** `guardar_gb` en `rockola.toml` (por defecto, 16) y
`systemctl --user restart rockola`. Es para todas las salas juntas, y siempre deja 2 GB libres en
la tarjeta: mirá con `df -h` cuánto hay disponible (más lo que ya ocupa `~/.cache/rockola`) antes
de subirlo.

## 11. Si algo falla

**El instalador tira `SSLEOFError` o `RemoteDisconnected` en el paso 2**
Son descargas que se cortan. `iniciar.sh` ya apaga piwheels en Pi OS de 64 bits (es lo que más
falla) y reintenta solo. Si sigue fallando, el problema es la red de la Pi:
- Volvé a correr `./pi/instalar.sh` (retoma donde quedó).
- Probá la conexión: `python3 -c "import urllib.request as u; print(u.urlopen('https://pypi.org/simple/pip/').status)"`
  tiene que decir `200`.
- Si estás por wifi, probá con cable. Algunos firewalls de oficina cortan estas descargas: probá
  desde otra red (por ejemplo, compartiendo internet del celular).

**No suena nada**
1. `./pi/audio.sh probar`: si el tono no suena, el problema es la salida. Elegí otra con
   `./pi/audio.sh usar N` y revisá el cable o el parlante.
2. Si el tono suena pero la música no: `journalctl --user -u rockola -f` y fijate qué dice al
   pedir un tema.

**Todas las canciones dicen "Salteada"**
Casi siempre es yt-dlp desactualizado (YouTube cambió algo):

```bash
cd ~/RockolaPi && .venv/bin/pip install -U "yt-dlp[default]" && systemctl --user restart rockola
```

**El Bluetooth se corta o suena entrecortado**
Wifi de 2,4 GHz + Bluetooth en la Pi se pisan: usá cable de red o wifi de 5 GHz. También ayuda
acercar el parlante.

**Los compañeros no pueden entrar a la página**
- ¿Están en la misma red que la Pi? Muchas redes de "invitados" **aíslan los dispositivos entre
  sí**; en esas no hay forma, hay que usar la red principal.
- Probá con la IP directa: `http://<ip>:8000` (la IP sale de `hostname -I`).
- Si el QR muestra una IP rara (por ejemplo, de una VPN), poné la correcta en `ip` de
  `rockola.toml`.

**La pantalla queda negra o en la consola**
- `systemctl --user status rockola`: ¿está corriendo el servidor?
- Revisá el login automático: `sudo raspi-config` → *System Options* → *Boot / Auto Login* →
  *Console Autologin*.
- Mirá el final de `~/.bash_profile`: tiene que estar el bloque `# rockola: TUI a pantalla completa`.

**Olvidé el PIN:** `grep pin ~/RockolaPi/rockola.toml`.

**Me dice "Demasiados intentos con PIN incorrecto":** esperá 5 minutos (es una protección contra
quien quiera adivinarlo).
