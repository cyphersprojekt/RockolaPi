# Configuración

Todo está en `rockola.toml`, en la carpeta de la rockola. `iniciar.sh` lo crea la primera vez a partir de
[`rockola.ejemplo.toml`](../rockola.ejemplo.toml), donde cada opción está explicada. Después de cambiarlo, reiniciá
la rockola para que lo tome (en la Raspberry: `systemctl --user restart rockola`).

| Opción | Por defecto | |
|---|---|---|
| `pin` | al azar | PIN para controlar desde la web (vacío = sólo desde la máquina) |
| `puerto` | `8000` | |
| `https_puerto` | `8443` | un segundo puerto con https y certificado propio, sólo para el 🎙️ micrófono (0: sin él) |
| `max_por_persona` | `3` | canciones pendientes por persona (y videos, en una sala de tv) |
| `duracion_maxima` | `600` | segundos (0 = sin límite) |
| `votos_para_saltear` | `3` | cuántas personas tienen que votar ⏭ para que la que suena se saltee sola (0 = no se vota) |
| `volumen` | `80` | volumen la primera vez (0–130); después queda el último que se dejó |
| `normalizar` | `false` | iguala el volumen entre canciones |
| `crossfade` | `6` | segundos que se pisan dos canciones con el 🎚️ crossfade prendido (se prende desde la web; 0 = no ofrecerlo) |
| `modo` | automático | `mpv` o `navegador` |
| `ip` | automática | la IP que muestran el link y el QR |
| `clima` | automático | de dónde es el clima de la barra de arriba: vacío, el de donde está la rockola (por su IP); una ciudad (`"Rosario"`) o un punto (`"-32.95, -60.64"`); `false`, no mostrarlo |
| `guardar_gb` | `16` | GB para canciones guardadas, entre todas las salas; al pasarlos se borran las que hace más que no suenan (0 = no guardar). Siempre deja 2 GB libres |
| `carpeta` | `~/.cache/rockola` | dónde quedan las canciones guardadas (y las copias de sus tapas y sus letras), las salas (también las de tv), las ♫ playlists, las estadísticas del recap y los 🧭 recomendados |
| `azar_al_prender` | `true` | al prender la Raspberry o reiniciar el servicio, si en 10 s nadie pide nada: 🔀 prendido y a sonar (con un parlante Bluetooth, cuando se conecta); si estaba sonando un tema, sigue ése. No en el reinicio de las 6 |
| `predescargar` | `0` | canciones de la cola que se bajan por adelantado (0 = toda la cola) |
| `[controles]` | | pines de los botones, paso de volumen y doble toque |

Los pines de los botones (`[controles]`) están explicados en la guía de los [botones](botones.md).

## Dos modos

Se elige con `modo` (o `--modo` al arrancar); sin decir nada, mpv si está instalado.

| | **mpv** (por defecto si está instalado) | **navegador** (`--modo navegador`) |
|---|---|---|
| Dónde suena | en la máquina del servidor | en la pestaña `localhost:8000/player` |
| Términos de YouTube | descargar el audio va contra los términos | usa el reproductor oficial |
| Mantenimiento | yt-dlp se actualiza solo una vez por día | ninguno |
| Canciones guardadas, ⬇ descargas y 🔀 | sí | no |
| 📻 Radio | sí | no |
| 🎚️ Crossfade | sí | no |
| 🚪 Salas | sí | no (hay una sola) |
| 📺 Salas de tv | sí | sí |
