# Por dentro

Cómo funciona cada parte, dónde queda cada cosa y qué se le manda a quién. Para usarla, ver
[Cómo se usa](uso.md).

## Qué hay en el repo

```
├── server.py             servidor: cola, búsqueda, API y el "director" que maneja mpv
├── reproductor.py        descargas con yt-dlp y control de mpv por su socket JSON IPC
├── estadisticas.py       lo que sonó, en SQLite, para el 📊 recap (y los géneros, de iTunes)
├── recomendados.py       los 🧭 artistas, álbumes y canciones recomendados de cada sala (recomendados.json)
├── listas.py             las ♫ playlists que se arman en la rockola, por sala (listas.json)
├── tapas.py              las copias de las tapas de las guardadas (para verlas sin internet)
├── letras.py             las copias de las letras de las guardadas (lo mismo)
├── voz.py                anuncios y micrófono abierto, y el certificado https
├── tele.py               las 📺 salas de tv: el video que va, su reloj, la cola y el chat (tv.json)
├── tui.py                pantalla de terminal (Textual)
├── controles.py          botones físicos (gpiozero)
├── static/               páginas web: pedidos, 🧭 recomendados, 📊 recap y ⬇ descargas (/), las salas de tv (/tv), /pantalla y /player
│   ├── piper/            🗣️ texto a voz (Piper, en el navegador): voz.js y fonemas.js
│   └── fuentes/          la letra de las páginas (JetBrains Mono, con su licencia OFL): la sirve la rockola
├── iniciar.sh, tui.sh    arrancar el servidor y la pantalla
├── rockola.ejemplo.toml  configuración de ejemplo
├── pi/                   instalador de la Raspberry, salida de audio y Bluetooth
├── hardware/             gabinete: generador paramétrico (manifold3d) y los STL
└── docs/                 guías e imágenes (captura_tui.py saca la de la terminal; capturas.py, las de
                          la web; diagrama.py, el diagrama de los botones)
```

## Cada parte

- **Búsqueda:** [ytmusicapi](https://github.com/sigma67/ytmusicapi), la API no oficial de YouTube Music.
- **Letras:** primero las de YouTube Music (por ytmusicapi). Si no tiene, o la tiene sin tiempos, se
  le pregunta a [LRCLIB](https://lrclib.net), una base libre de letras con tiempos (sin cuenta ni
  clave): primero el tema exacto (título, artista, álbum y duración) y, si no está, por título y
  artista. Sus tiempos se usan sólo si la duración coincide (±3 s); si no, es otra versión y los
  renglones irían corridos, así que queda sin tiempos. Se le manda el título, el artista, el
  álbum y la duración de lo que suena, nada más.
  La letra de cada canción guardada queda copiada en `~/.cache/rockola/letras` (un `.json` por
  canción, un par de KB): la que está copiada no se vuelve a preguntar, y sale aunque no haya
  internet. Se copia cuando alguien la pide (al sonar) y, las de las guardadas que todavía no
  sonaron, de a una en segundo plano y bien despacio (una cada 10 segundos: con mil guardadas,
  la primera vez son unas tres horas, y sigue después de un reinicio; sin internet, espera).
  La que no tiene letra, o la tiene sin tiempos, también queda anotada, pero vence al mes: se
  vuelve a preguntar la próxima vez que suene, por si la subieron. Las de canciones que ya no
  están guardadas se borran al arrancar.
- **Reproducción (modo mpv):** el *director* (en `server.py`) mira el estado y hace que mpv toque lo
  que corresponde. Cada tema se baja con [yt-dlp](https://github.com/yt-dlp/yt-dlp) (~5 MB).
  Mientras suena uno, se bajan de a una, en orden, todas las que siguen en la cola: si la mezclan o
  la reordenan, sigue con el orden nuevo; si al disco le quedan menos de 2 GB, frena. Como mucho hay
  dos descargas a la vez (para no ahogar a una Pi con poca memoria): la de la canción que tiene que
  sonar ya, si no estaba, y la de adelanto. Si mpv se cae o se cuelga, se reinicia y retoma la
  canción en el segundo en que iba. Para retomar, la carga en pausa, salta con `seek` (anda igual
  en todas las versiones de mpv) y recién ahí la suelta: no se oye el principio.
- **Temas y videos:** en YouTube Music un tema puede ser el audio del disco o su video oficial,
  que es otra grabación (con la intro del video) y a veces tiene restricción de edad: ésos no se
  bajan sin una cuenta. Por eso, de un álbum se toman los temas de su lista «de audio», no los
  videos (salvo los que ya estaban guardados con el id del video, para que no queden repetidos).
  Y si igual toca bajar un video que pide confirmar la edad (un tema suelto, un link pegado, una
  playlist), se busca el mismo tema en versión de audio (en su álbum o, si no se sabe cuál es,
  por título y artista) y se baja ése: queda guardado como el que se pidió.
- **Crossfade:** mpv no mezcla dos archivos, así que con el crossfade prendido hay dos mpv. Cuando
  a la canción que suena le queda lo que dura el crossfade (y la que sigue ya está bajada), el
  otro mpv la arranca con el volumen en 0 y pasa a ser «el que suena»: el estado, el progreso y
  la pausa son los suyos. En lo que le queda a la anterior, el volumen de uno sube como un seno y
  el del otro baja como un coseno (potencia constante: no se nota un bajón en el medio), con la
  raíz cúbica de por medio porque el volumen de mpv va al cubo. Al terminar, el que tocaba la
  anterior queda libre para el próximo cambio. Durante el crossfade el volumen que se ve en la
  página es el del estado, no el de cada mpv.
- **Canciones guardadas:** quedan en `~/.cache/rockola/canciones`, el audio y un `.json` con sus
  datos (título, artista, álbum, duración y tapa). La fecha del archivo marca la última vez que
  sonó: no hay un índice que se rompa si se corta la luz. En 16 GB entran unas 3000. El álbum sale
  de YouTube Music o, para las pedidas por link, de yt-dlp; a las que se guardaron antes de que se
  anotara, se lo pregunta de a una, sin apuro, un rato después de arrancar.
- **Tapas:** la tapa de cada tema es una dirección en los servidores de Google. De las canciones
  guardadas la rockola se baja una copia a `~/.cache/rockola/tapas` (de 120 px, unos 8 KB: lo
  que hace falta para el tamaño en que se muestran; los temas de un mismo disco comparten
  archivo), y en todo lo que les contesta a las páginas y a la pantalla cambia la dirección de
  una tapa copiada por la de la copia (`/tapas/…`). Así se ven aunque se corte internet, cargan
  de la red de la oficina y pesan lo mismo vengan de donde vengan (la misma tapa llega de 120 px
  desde la búsqueda y de 544 px, diez veces más pesada, desde un álbum). Se copian al guardarse
  cada canción; las de las que ya estaban, de a una, al arrancar (sin internet, esperan a que
  vuelva). Lo anotado en cada canción sigue siendo la dirección original, y las copias de las
  que ya no están guardadas se borran al arrancar. No cuentan para `guardar_gb`: mil tapas son
  unos 8 MB. Las de lo que todavía no está guardado (los resultados de YouTube) vienen de
  Google, como siempre.
- **Deno** se instala en el entorno virtual para que yt-dlp resuelva los desafíos de JavaScript
  de YouTube.
- **📻 Radio:** no transmite el audio de la Pi (eso pediría ffmpeg y sumaría segundos de demora). La
  página baja de `/api/audio/<id>` el mismo archivo que está tocando mpv (sólo ese: no sirve para
  bajarse las guardadas) y lo hace sonar en el segundo que dice el estado; si se corre más de medio
  segundo, salta adonde va. Son unos 5 MB por tema y por oyente, por la red de la oficina.
- **🚪 Salas:** cada sala tiene lo suyo (cola, historial, chat, 🔀, avisos) y todas las páginas,
  la TUI y los botones le hablan a la misma API, con `sala=<id>` (sin nada: la principal). En la
  principal, mpv dice por dónde va; en las otras lo lleva un reloj en el servidor (el *director*
  baja la que toca, cuenta el tiempo y pasa a la siguiente), que corre sólo mientras alguna 📻
  está prendida: cada página con la radio prendida lo dice al pedir el estado, y si no se sabe
  nada de ella en 40 s se la da por apagada. Las salas (nombre, PIN con hash y 🔀) quedan en
  `~/.cache/rockola/salas.json`; la cola, en `colas.json` (ver abajo); el chat, en memoria. Cada guardada anota de qué salas
  es (`salas` en su `.json`; las de antes de las salas, de la principal). Cuando falta lugar se
  borran primero las que no son de ninguna (las de salas borradas).
- **📺 Salas de tv:** el servidor no toca el video: lleva la cuenta de cada sala (`tele.py`: qué
  video va, por qué segundo según un reloj, si está en pausa, la cola y el chat) y cada página hace
  que su reproductor de YouTube (el oficial, el mismo de `/player`) vaya a la par: carga ese video
  y, si se va más de un segundo y medio del reloj, salta adonde va. Al revés, cuando alguien toca
  el video de su página (pausa, play, lo mueve), la página se lo cuenta al servidor y de ahí les
  llega a las demás. Como el reproductor avisa igual sus cambios, los haya pedido la página o una
  persona, la página sólo toma por de una persona lo que pasa cuando el video ya estaba como decía
  la sala y dura un rato: así dos páginas no terminan pausándose entre sí, y un video frenado por
  el navegador (el celular con la pantalla bloqueada, una publicidad) no les pausa a todos. El reloj
  de un video arranca cuando la primera página avisa que ya lo tiene andando, y corre sólo
  mientras alguna tenga el video a la vista (lo dice al pedir el estado; a los 40 s sin noticias se
  la da por cerrada). La búsqueda es la de YouTube (con yt-dlp: sólo la lista de resultados, sin
  bajar nada), porque la de YouTube Music trae sólo música. Tienen su propio lock: lo que pasa en
  una sala de tv no despierta a la música. Quedan en `~/.cache/rockola/tv.json`.
- **📊 Estadísticas:** cada vez que un tema deja de sonar (terminó, ⏭, ▶ ahora…) se anota en `~/.cache/rockola/estadisticas.db` (SQLite, sin nada que instalar): la sala,
  cuánto sonó de verdad (no lo que dura), quién lo pidió y si fue pedido, de una playlist o 🔀. Lo
  escribe un hilo aparte, para no frenar la música. Si la base se rompe (un corte de luz justo
  mientras escribe), queda a un costado como `estadisticas.rota-<fecha>.db` y se empieza otra.
- **Géneros:** YouTube Music no los da. Se le pregunta a la [API de búsqueda de
  iTunes](https://performance-partners.apple.com/search-api) (pública, sin cuenta ni clave) por el
  primer artista de cada tema, de a uno cada 4 segundos, y queda anotado. Sólo se usa si el nombre
  coincide, así un video subido por cualquiera no se lleva un género inventado; esos quedan «sin
  género» (se vuelve a preguntar al mes). Se le manda el nombre del artista, nada más. Se
  pregunta por los artistas de lo que sonó (para el recap) y por los de las guardadas, aunque no
  hayan sonado nunca (para verlas **🎸 por género**): primero los más escuchados. Con muchas
  guardadas, la primera vez tarda (unos 15 artistas por minuto).
- **Clima:** de [Open-Meteo](https://open-meteo.com) (gratis, sin cuenta ni clave). Se le manda
  dónde, redondeado a un kilómetro: la ciudad de `clima` (la ubica el buscador de lugares del
  mismo Open-Meteo) o, sin configurar, el lugar que [ipwho.is](https://ipwho.is) (o
  [GeoJS](https://www.geojs.io), si no contesta) dice que le corresponde a la IP pública de la
  rockola; a ésos no se les manda nada: ven de dónde viene el pedido. Lo pregunta la rockola, no
  cada página. Si no puede (no hay internet), queda el último hasta dos horas y después nada.
- **Tiempo real con long-poll:** las páginas, la TUI y los botones se enteran de cada cambio al
  instante, sin websockets ni dependencias. Para que eso sea liviano: lo que la rockola manda
  (las páginas, el estado, las guardadas, una búsqueda) va comprimido con gzip si el navegador
  lo acepta (la página de pedidos pasa de unos 180 KB a 50; se comprime una sola vez), y la
  página arma de nuevo sólo lo que cambió: un mensaje en el chat o un cambio de volumen no
  vuelven a dibujar la cola.
- **HTTPS y micrófono:** los navegadores sólo dejan usar el micrófono en páginas seguras, así
  que la rockola abre un segundo puerto (`https_puerto`, 8443) con un certificado autofirmado,
  que hace sola con `openssl` la primera vez (y de nuevo si cambia la IP; queda en
  `~/.cache/rockola/https`). Es un certificado propio: el navegador avisa una vez, y la página
  sólo manda ahí a quien toca **abrir por https** para hablar; los demás siguen por `http://`
  sin ninguna advertencia. El micrófono llega en pedacitos (MediaRecorder) por POST y un `mpv`
  aparte lo toca en vivo; los anuncios los toca otro `mpv`. Todo exige el PIN.
- **La letra de las páginas** ([JetBrains Mono](https://www.jetbrains.com/lp/mono/), licencia
  OFL) la sirve la rockola misma, desde `static/fuentes/` (66 KB entre todos los alfabetos; el
  navegador baja sólo los que usa, casi siempre los 31 KB del latino, y los guarda). No se le
  pide a Google Fonts: así la página no depende de internet para dibujarse. Si el celular está
  en el wifi de la oficina y se corta internet, aparece igual al toque, y con las tapas de las
  guardadas (ver **Tapas**).
- **Permisos:** cualquiera agrega temas, quita los suyos, vota, reacciona, chatea, crea salas y arma
  ♫ playlists (las cambia quien la armó: la página la reconoce por el mismo identificador con el
  que quita sus temas; es también el que cuenta como «una persona» para los votos y las
  reacciones: es por navegador, no por nombre). Cargar un álbum o una playlist entera es con el PIN: si no, una persona llenaría la cola
  de una, sin el límite por persona. Lo demás se hace
  desde la misma máquina o con el PIN: el de la rockola, que vale en todas las salas, o el de la
  sala (5 intentos fallidos bloquean esa IP 5 minutos). Las páginas no pueden mandar pedidos de
  control desde otro sitio: el servidor exige JSON y revisa el `Host`. En una sala de tv el
  control del video (pausa, moverlo, ⏭) es de todos; el PIN es para la cola de otros y para borrarla.
- **Entre reinicios:** de cada sala, lo que suena (y en qué segundo), la cola con quién pidió cada
  tema y lo que sonó recién quedan en `~/.cache/rockola/colas.json`; ahí mismo, las canciones que
  esperan para bajarse (⬇), en su orden. Se escribe un par de segundos
  después de cada cambio, cada 30 s mientras suena algo (por si se corta la luz: no más seguido,
  para no gastar la tarjeta) y al apagar la rockola. Las salas, las canciones guardadas, el 🔀,
  las ♫ playlists (`listas.json`, con los datos de cada tema: se ven sin preguntarle a YouTube) y
  las estadísticas también quedan; el chat vive en memoria y se borra. El tema que sonaba cuando
  se apagó se anota en las estadísticas una sola vez, cuando termina después de retomarlo.
