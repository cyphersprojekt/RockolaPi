# Cómo se usa

Todo lo que se hace desde la página, la pantalla y la terminal. Para ponerla en marcha, ver el
[README](../README.md) y la guía de la [Raspberry Pi](raspberry.md); las opciones, en [Configuración](configuracion.md).

- [La página](#la-página)
- [Pedir, votar y reaccionar](#pedir-votar-y-reaccionar)
- [Canciones guardadas y descargas](#canciones-guardadas-y-descargas)
- [Playlists](#playlists)
- [Recomendados y recap](#recomendados-y-recap)
- [Con el PIN](#con-el-pin)
- [Salas, radio y chat](#salas-radio-y-chat)
- [Si se reinicia o se corta internet](#si-se-reinicia-o-se-corta-internet)
- [La pantalla, la terminal y el clima](#la-pantalla-la-terminal-y-el-clima)

## La página

Los seis botones de la barra de arriba (**📻** radio, **🧭** recomendados, **📊** recap, **⬇** descargas,
**🎙️** voz y **🔒** controlar) son sólo el ícono: qué es cada uno lo dice al pasarle el mouse, y el que está prendido
(la radio sonando, la vista abierta, el PIN puesto) lleva el recuadro resaltado. En el celular
no están arriba: van en un menú fijo, abajo, al alcance del pulgar. En las vistas que no son la de
la cola (🧭 📊 ⬇ 🎙️), abajo de todo queda un **mini reproductor**, como en las apps de música: lo que
suena y por dónde va (con el PIN, ⏮ ⏸ ⏭); tocándolo se vuelve a la cola.

## Pedir, votar y reaccionar

- **Compañeros:** entran al link o escanean el QR, ponen su nombre, buscan y tocan **+ agregar**.
  Hay un solo buscador, sin botón: mientras escribís aparecen las **💾 guardadas** que coinciden
  (las palabras en cualquier orden) y, cuando dejás de escribir, abajo de ellas lo que hay en
  YouTube (con Enter, sin esperar). Las guardadas suenan sin esperar la descarga. En la compu el
  panel del buscador tiene su alto aunque esté vacío, y los resultados lo van llenando (si no
  entran, se paginan): así buscar no mueve lo que está abajo.
- **💿 Álbumes:** buscando un disco o un artista («bocanada», «soda stereo») aparecen también sus
  álbumes, con cuántos temas hay guardados. Tocando uno se ven sus temas, para pedirlos de a uno
  o, con el PIN, **▶ reproducir** el disco entero (corta la que suena; lo que estaba en la cola
  sigue después) o ponerlo **+ a la cola**, al final.
- **👍 Votar la cola:** al lado de cada canción de las próximas hay un **👍** (en las que pidieron
  los demás: la tuya no la votás vos). La votada sube hasta quedar debajo de las que tienen
  tantos votos como ella, o más; entre dos con los mismos votos, va primero la que los juntó
  antes. Es un voto por persona y por canción, y no se saca. En la tuya ves cuántos tiene. Con el
  PIN se sigue pudiendo ordenar a mano: a una que pusieron en su lugar con ⤒ o arrastrándola
  (↕), los votos de las otras no le pasan por arriba.
- **⏭ Saltear por votos:** debajo de lo que suena, **⏭ 0/3**: es un voto para saltearla. Cuando
  votan 3 personas (`votos_para_saltear` en `rockola.toml`; con 0 no se vota), pasa sola a la
  que sigue y avisa. Quien tiene el PIN la saltea sin votar, como siempre. Cada voto queda
  anotado en el chat, con el nombre de quien votó: por eso, para votar hay que tener puesto el
  nombre (arriba), como para pedir un tema.
- **🔥 ❤️ 😴 Reacciones:** al lado del ⏭, para decir qué te parece lo que suena: una de cada una
  por persona y por canción, y tocándola de nuevo se saca. Todos ven cuántas van; en la pantalla
  de la Raspberry (y en `/pantalla`) además aparece un instante de quién fue la última, y en el
  chat queda anotado quién reaccionó: para reaccionar también hay que tener puesto el nombre
  (para sacar una reacción propia, no). Cuando la canción termina, las reacciones que le
  quedaron van con ella a «sonaron recién» y al 📊 recap.
- **Letra:** la de la que suena (a la izquierda en la compu; debajo de lo que suena en el
  celular), de YouTube Music o, si ahí no está o no trae los tiempos, de [LRCLIB](https://lrclib.net).
  El título dice de dónde vino (LyricFind o Musixmatch, que le dan las letras a YouTube, o
  LRCLIB). Si trae los tiempos, se va poniendo amarilla mientras se canta y se mueve sola; si
  no, queda para leerla. La de una canción guardada queda copiada en la rockola: aparece al
  toque, y también si se cortó internet.

## Canciones guardadas y descargas

En modo mpv: lo que suena queda guardado en la tarjeta, y también se puede guardar sin pedirlo.

- En **GUARDADAS** (abajo de todo en el celular; en la compu, a la derecha, debajo del buscador)
  están todas las que ya se bajaron, para recorrerlas. En el desplegable **ver:** se elige cómo:
  **💿 por álbum** (tocás uno y ves sus temas), **🎤 por artista**, **🎸 por género** o
  **🕘 recientes**, separadas en páginas (ahí mismo, **♫ playlists** muestra las playlists). Con el
  PIN, **🗑** borra una a mano: sale de las guardadas de la sala y, si ninguna otra sala la tiene,
  se borra el archivo (si está sonando o en la cola, cuando termine). Si vuelve a sonar, se vuelve
  a guardar. El mismo **🗑** está en el buscador, en las que ya están guardadas (ahí va en lugar
  de **⬇ guardar**): no hace falta ir a buscarla a las guardadas para borrarla.
- **🎤 Por artista:** las guardadas de cada artista, en orden alfabético (un tema de varios, como
  «Bizarrap, Quevedo», está en el de cada uno). Tocás uno y elegís qué ver: **💿 álbumes** (los
  discos en los que está, que se abren como cualquier álbum; sus temas sueltos van al final, como
  «Sin álbum») o **🎵 canciones** (todas las suyas, juntas). Lo que elijas queda para la próxima.
  Con el PIN, **▶ reproducir** o **+ a la cola** todo lo suyo, mezclado (hasta 200 temas).
- **🎸 Por género:** otra forma de ver las guardadas. Tocás un género y ves sus temas (por artista),
  para pedirlos de a uno o, con el PIN, **▶ reproducir** o **+ a la cola** el género entero:
  van mezclados, y si son más de 200, 200 al azar (cada vez, otros). El género es el del artista
  (ver **Géneros** en [Por dentro](por-dentro.md)): se va averiguando de a un artista, así que al principio muchas están
  en **Sin género** (ahí dice cuántos artistas faltan) y la lista se acomoda sola. En «Sin género»
  quedan los videos sueltos y los artistas que iTunes no conoce.
- **Guardadas de cada sala:** cada sala ve en GUARDADAS, en lo que sugiere el buscador y en el 🔀
  sólo las suyas: las que sonaron en ella y las que guardó con ⬇. Si pedís (o guardás con ⬇) una
  que ya bajó otra sala, suena sin esperar y pasa a ser también de la tuya. El espacio
  (`guardar_gb`) es uno solo para todas.
- **Guardar sin pedir:** en los resultados de YouTube, **⬇ guardar** la baja para que quede entre
  las guardadas, sin ponerla en la cola (en modo mpv). Se bajan de a una, después de la que tiene
  que sonar; hasta 100 esperando a la vez (el lote de **📚 biblioteca** no cuenta), con las mismas reglas que para pedir (nada en vivo ni
  más largo que `duracion_maxima`). Lo que espera se ve en **⬇ descargas** (abajo).
- **Guardar un álbum entero:** en esos álbumes (modo mpv), **⬇ guardar** baja todos sus temas
  (o **⬇ completar**, los que faltan), sin ponerlos en la cola; mientras, dice cuántos faltan y
  al terminar avisa una sola vez. Un álbum a medias también se completa desde GUARDADAS: abrilo y
  tocá **⬇ completar** (lo busca en YouTube Music por nombre y artista). Ahí también está
  **▶ reproducir** (con el PIN): el disco entero, en el orden de YouTube Music, aunque falten temas
  por bajar (si YouTube no lo encuentra, suenan los guardados).
- **Guardar una playlist de YouTube:** al pegar su link en el buscador, la tarjeta de la playlist
  trae **⬇ guardar** (además de cargarla a la cola): baja todos sus temas para que queden entre
  las guardadas, sin ponerlos en la cola, igual que con un álbum (la cuenta de lo que falta, el
  reintento y **⬇ descargas** son los mismos), y arma con ellos una **♫ playlist de la rockola**
  con el mismo nombre. Si ya hay una con ese nombre en la sala, le suma los que faltaban; si es
  de otra persona (y no tenés el PIN), baja igual y avisa que no pudo tocarla. Lo que no se
  puede pedir (en vivo, demasiado largo, no disponible) queda afuera de las dos cosas.
- **⬇ Descargas:** arriba (en el celular, en el menú de abajo), **⬇** cambia la cola por la lista de lo que espera para
  bajarse, en la misma página, como el recap (el botón lleva la cuenta en la esquina; tocándolo
  de nuevo se vuelve; el link directo es `http://<ip>:8000/descargas`). Están la que se está
  bajando, las que esperan, en el orden en que se van a bajar, y las que fallaron (el aviso dura
  un minuto). **✕** cancela una (o **✕ todas**): cada uno las suyas; con el PIN, cualquiera. Si
  ya se estaba bajando, termina pero no queda guardada. Con el PIN, **⤒ ↑ ↓** cambian el orden:
  **⤒** la pone primera (después de la que ya se está bajando). Las que esperan **quedan
  anotadas**: si la rockola se reinicia o se corta la luz, al volver sigue bajándolas, en el
  mismo orden. Y si no hay internet (p. ej. recién prendida la Raspberry), no se pierden:
  quedan esperando y se prueba de nuevo cada medio minuto. La fila es una sola para todas las
  salas; cada sala ve y ordena las suyas.
- **📚 Biblioteca y lotes (para dejar muchos álbumes bajando, p. ej. un fin de semana):** en **⬇ descargas**,
  la pestaña **📚 biblioteca** lista todos los álbumes guardados, con un filtro y una casilla en cada
  uno (con **shift** se elige un tramo; **☑ todos** elige los que se ven con el filtro, de todas las
  páginas). La lista va de a 50 álbumes (‹ ›), y un desplegable elige cuáles ver: **todos**,
  **incompletos** (les faltan temas), **completos** o **sin datos**. Para saberlo, la rockola le
  pregunta a YouTube Music, de a un álbum y sin apuro, qué temas tiene cada uno (el primer barrido de
  cientos de álbumes tarda un rato, y se ve «revisando: faltan N»); queda anotado en `albumes.json` y
  después «faltan» se calcula contra lo guardado, así que baja solo a medida que se completan. Un tema
  que YouTube no deja bajar sigue contando como faltante. **Qué le falta:** al abrir un álbum (en GUARDADAS o
  con ▾ en la biblioteca), después de sus temas guardados aparecen, apagados y con «falta» y su ⬇, los que YouTube
  Music tiene y todavía no están guardados (hace falta internet; sin ella no aparece nada). **Borrar:** con el PIN, **🗑 borrar N** saca de las
  guardadas de la sala los álbumes elegidos con todos sus temas, y **▾** en cada álbum despliega sus temas, cada
  uno con su 🗑 (y «🗑 el álbum entero»); pregunta antes, y el archivo se borra si ninguna otra sala lo tiene. Con el PIN, **⬇ completar N**
  les busca en YouTube Music lo que les falta y lo baja **sin el tope de la fila** (los pedidos de los
  demás pasan adelante). Va de a poco: no pone más de 150 temas del lote en la fila a la vez, y a
  medida que se bajan sigue con el próximo álbum. Sigue solo si la rockola se reinicia (el reinicio de las
  6 de la mañana, p. ej.). Si se llena el espacio de `guardar_gb` el lote **espera** en lugar de borrar
  canciones viejas. En **⬇ en curso** se ve cómo va (arriba a la derecha), los álbumes que no se
  encontraron, y **✕ el lote** lo cancela.

## Playlists

- **♫ Armar una playlist:** en el panel de abajo a la derecha (abajo de todo en el celular),
  **♫ playlists** (en el desplegable **ver:**) y **+ nueva playlist**. Queda elegida para sumarle
  temas: aparece **♫+** al lado de cada tema (en la búsqueda, en un álbum abierto, en las
  guardadas, en lo que suena, en la cola y en lo que sonó) y **♫+ todos** en un álbum o una
  playlist de YouTube. **♫✓** quiere decir que ya está (tocalo para sacarlo); **✓ listo** termina.
  Abriéndola se ve en orden, se suben temas (↑), se sacan (✕), se le cambia el nombre (✎) o se
  borra (🗑): eso, quien la armó (desde el mismo navegador) o alguien con el PIN. Cualquiera pide
  sus temas de a uno; entera, **▶ reproducir** o **+ a la cola**, con el PIN. Cada sala tiene las
  suyas (hasta 50, de hasta 200 temas cada una).
- **Poner una playlist de YouTube:** con el PIN, pegá su link en la búsqueda y tocá **+ a la cola**
  (o **▶ reproducir**, para que suene ya). Sus temas van al final de la cola (como mucho 200; se
  saltean los repetidos y los muy largos), así que lo que pidan después suena cuando termine la
  playlist; con ⤒ se adelanta un pedido. Lo mismo con un álbum o una ♫ playlist propia. Arriba de
  la cola aparece cada una que se cargó (♫ o 💿) con **✕ quitar todos**, para sacarla entera.

## Recomendados y recap

- **🧭 Recomendados:** como en las apps de música, **🧭** cambia la cola por tres columnas: **artistas**,
  **álbumes** y **canciones** que podrían gustarle a la sala (su link es `http://<ip>:8000/recomendados`).
  Salen de lo que más suena (lo pedido cuenta más que lo que eligió 🔀, lo de hace poco más que lo de
  hace meses, y 🔥 ❤️ suman y 😴 resta) y de lo que más hay guardado: los artistas que YouTube Music da
  como parecidos (uno parecido a varios de los de la sala sube), los discos más escuchados de esos
  artistas y los de la sala que no están, y temas de las radios de lo que más sonó. Lo que ya está
  guardado o ya sonó no aparece, y de cada artista van dos como mucho. Cada una dice de dónde salió
  («como Soda Stereo y Cerati», «de tus artistas», «por «De música ligera»»). Tocando un artista se ven
  sus temas más escuchados y sus álbumes, y tocando un álbum, sus temas, en la misma columna (‹ vuelve):
  **+** a la cola, **⬇** guardar y, con el PIN, **▶** ahora o el disco entero.
  - Es caro (unas 30 preguntas a YouTube la primera vez), así que no es en el momento: la rockola los
    arma sola, sin apuro, y los renueva **cada 2 horas** (la principal siempre; otra sala, mientras
    alguien los haya mirado en la última semana). Lo que le preguntó a YouTube de un artista vale un
    día, así que las vueltas de cada 2 horas casi no le piden nada: vuelven a ordenar con lo que sonó y
    se guardó mientras tanto, y un poco al azar entre los mejores, para que cambien. La primera vez
    tardan un minuto en aparecer; quedan en `recomendados.json` (en la carpeta), así que después de
    reiniciar ya están, aunque no haya internet.
- **📊 Recap:** arriba (en el celular, en el menú de abajo), **📊** cambia la cola por el recap, en la misma página: la 📻 radio sigue
  sonando, y se vuelve tocándolo de nuevo (o con ← del navegador). Su link es
  `http://<ip>:8000/recap`, y se actualiza solo cuando termina un tema. Muestra cuánto sonó, el top de
  canciones, artistas y géneros, **quién pide más** (con su artista favorito), a qué hora y qué día
  suena más, el día récord y la más salteada. Arriba se elige el período (hoy, 7 días, 30 días, cada
  año, todo) y, si hay varias salas, cuál (o **∑ todas**). Tocando un nombre se ve lo que pidió esa
  persona; **👤 lo mío**, lo tuyo (con el nombre que pusiste). Cuenta como escuchada una canción que
  sonó al menos 30 segundos (las más cortas, casi enteras): las que se cortan antes suman tiempo y
  cuentan como pedidas, pero no para los rankings. En «quién pide más» no entran 🔀 ni lo que se
  cargó entero (playlists y álbumes). Entre los datos están **la más festejada** (🔥), **la más
  querida** (❤️) y **la que durmió a todos** (😴): de cada reacción, la canción que más juntó. Las de cada persona se juntan por el nombre, sin fijarse en mayúsculas ni acentos.

## Con el PIN

- **Quien maneja la música:** en la misma página, **🔒** y el PIN, en un cuadro que sale encima de lo
  que se esté mirando (la cola, 🧭, el recap…: al entrar se sigue ahí). Mientras se controla queda
  **🔓**, resaltado: tocándolo se deja de controlar. Aparecen ⏮ ⏸ ⏭ con el
  volumen al lado, abajo 🔀 y 🎚️ (lo que se deja prendido o apagado), y en cada canción de las
  próximas **⤒** (que suene a continuación), **↕** y **✕** (quitarla). De **↕** se la agarra
  para **arrastrarla** adonde tiene que ir, con el mouse o con el dedo: mientras, las otras se
  corren para mostrar dónde va a quedar (Esc, y vuelve a su lugar). Con el teclado, ↑ y ↓ sobre
  **↕** la mueven de a un lugar. En la compu, si la cola tiene varias páginas, se arrastra dentro
  de la página. Al lado de cada
  **+ agregar** (en la búsqueda y en las guardadas) aparece **▶ ahora**: corta la que suena (queda
  en "sonaron recién", como con ⏭) y pone esa; la cola sigue igual.
- **Mezclar la cola:** con el PIN, **🔀 mezclar** arriba de las próximas. Las pone en otro orden al
  azar, una vez: lo que pidan después va al final, como siempre. Sirve, por ejemplo, después de
  cargar una playlist (ojo, que mezcla también los pedidos que ya estaban).
  Con el aleatorio prendido, **🔄** (al lado) cambia las tres elegidas al azar por otras tres.
- **Música de fondo:** con el PIN, **🔀** (al azar: abajo a la derecha de los controles, chiquito).
  Con el 🔀 prendido, **PRÓXIMAS** siempre muestra tres temas: los que pidieron y, para completar, los
  que el azar ya eligió (más apagados, con «🔀 al azar»), así se ve qué viene. Lo que piden va
  reemplazando a los elegidos desde el primero y deja los otros: con [azar1, azar2, azar3], una
  pedida deja [pedida, azar2, azar3]; otra, [pedida1, pedida2, azar3]; con tres pedidas ya no se
  muestra ninguno al azar. Cuando suena una pedida, el elegido que había desplazado se va y entra
  uno nuevo al final. Los elegidos no se votan, mueven ni quitan.
  **De dónde elige el 🔀:** el botón de al lado (también chiquito) pasa entre **📁 offline** (las
  guardadas, como siempre), **🌓 mixto** (la mitad de las elegidas) y **🌐 online** (las que YouTube
  Music da como relacionadas con la que suena, su «radio»; las pedidas siguen yendo primero). Las de
  YouTube se bajan por adelantado, igual que la cola, y **quedan guardadas**; en PRÓXIMAS dicen «🌐 de
  YouTube». **Sin internet** (el led de arriba) elige solo entre las guardadas, y el botón se apaga un
  poco; cuando vuelve internet, vuelve a lo que estaba elegido. Si YouTube no da nada, también sigue con
  las guardadas: nunca se corta. Se recuerda por sala (`ajustes.json` / `salas.json`).
  La pantalla de la Raspberry (la página `/pantalla` y la TUI) muestra arriba «🔀 📁 offline», «🔀 🌓 mixto»
  o «🔀 🌐 online», y en rojo «(sin internet: guardadas)» cuando se cayó solo.
  Cuando la cola se vacía, suenan canciones que ya sonaron alguna vez (sin repetir las de
  recién); lo que pidan va primero, cuando termina la que está sonando. **Al prender la Raspberry, o al reiniciar la rockola (p. ej. al actualizarla), si
  en 10 segundos nadie pidió nada, se prende solo y arranca a sonar** (con un parlante Bluetooth,
  en cuanto se conecta); se apaga con `azar_al_prender = false`. Si cuando se apagó estaba sonando
  un tema, en lugar del 🔀 sigue ése, donde iba (ver [Si se reinicia](#si-se-reinicia-o-se-corta-internet)). En el reinicio
  diario de las 6 no arranca solo: arranca cuando se termina la cola, al prenderlo de nuevo, o con
  ⏯ (también el de la botonera). **Ojo:** si se van todos, sigue sonando hasta que lo pausen.
- **🎚️ Crossfade:** con el PIN, **🎚️** (al lado de 🔀). Prendido, cuando a una
  canción le quedan unos segundos la que sigue arranca bajito y va subiendo mientras esa baja:
  se pisan 6 segundos (`crossfade` en `rockola.toml`) y el volumen del conjunto no se mueve.
  Queda como lo dejaste, también después de reiniciar. Algunas cosas a saber:
  - Para la página, la canción que entra «ya suena» desde que arranca: la anterior pasa a
    «sonaron recién» unos segundos antes de terminar.
  - Es entre una canción que termina sola y la que sigue (de la cola, o del 🔀). Con ⏭, ⏮ o
    **▶ ahora** el cambio es de golpe, como siempre; si pasa en pleno crossfade, se cortan las dos.
    Con ⏸ esperan las dos.
  - No hay crossfade si la que sigue todavía se está bajando, ni con canciones muy cortas (menos
    de cuatro veces lo que dura el crossfade).
  - Es del parlante. Por **📻 radio** se escucha un corte: la que sigue arranca esos segundos
    antes, sin mezcla. Las salas sin parlante no tienen.
  - Mientras está prendido hay un segundo mpv esperando (unos 30 MB de memoria), y hace falta un
    sistema de audio que mezcle (PipeWire o PulseAudio: lo que deja `pi/instalar.sh`). Si la
    salida no deja sonar dos cosas a la vez, se apaga solo y avisa.
- **🎙️ Voz** (con el PIN de la sala; **cada sala tiene lo suyo**: sus anuncios, su micrófono, sus
  «cada X canciones»): **🎙️** en la barra de arriba cambia la cola por
  la pestaña **VOZ**, como el recap o ⬇ descargas (apagado sin el PIN; su link es `/voz`), con dos cosas:
  - **Micrófono abierto:** hablás desde tu celular o compu y se escucha en el parlante, con la
    música más baja (a un cuarto) hasta que cerrás; si dejan de llegar datos, se cierra solo.
  - **Anuncios:** **⏺ grabar un anuncio** (o **＋ subir audios**: mp3, m4a, ogg, wav… hasta 30 MB cada uno, de a varios; también sirve el audio de un video) y elegís **cada X minutos**
    y/o **cada X canciones**; se repite solo, encima de la música (que baja un rato). Cada uno
    se puede pausar (↻/⏸), probar (▶) o borrar (🗑), o pasar a **🎲 al azar**: suena en momentos
    impredecibles, en promedio cada X minutos (puede sonar dos veces seguidas o tardar una hora:
    ideal para memes y sustos), o a **🕐 horas fijas**: escribís `14:00, 18:30` (todos los días a esa
    hora) o `*:00` (al minuto 0 de cada hora, «en punto»; `*:30` a y media), y suena sólo a esos
    horarios (si en ese minuto la sala está ocupada con otro anuncio o el micrófono, espera al
    próximo). Quedan en `~/.cache/rockola/anuncios`.
  Los navegadores sólo dejan usar el micrófono en páginas **https**, así que la rockola abre un
  segundo puerto (`https_puerto`, 8443) con un certificado propio, que se hace solo con openssl.
  Los demás entran por `http://` como siempre y no ven ninguna advertencia: el panel, por http,
  ofrece **abrir por https** (el navegador avisa una vez que el certificado es propio: se acepta)
  y recién ahí aparecen el micrófono y grabar. Subir un audio ya hecho y manejar los anuncios
  andan por http.
  **🗣️ Generar tts:** el botón de al lado de grabar y subir abre un modal para escribir un texto y que
  una voz lo diga; se escucha antes de guardarlo, y queda como un anuncio más. Se puede elegir la
  **voz** (Daniela, argentina; y otras en español de México y España; Sharvard trae dos hablantes), la
  **velocidad**, el **volumen** (con «normalizar», que sube el audio al máximo antes: la voz sale baja) y,
  en «más ajustes», la variación y el ritmo de la voz, la pausa entre frases y el silencio al principio
  y al final. Los ajustes se recuerdan en el navegador. La voz ([Piper](https://github.com/rhasspy/piper))
  se genera **en el navegador de quien lo usa**, no en la Raspberry: ésta sólo recibe el audio ya
  hecho. La primera vez con cada voz el navegador la baja (28 a 114 MB, de HuggingFace) y el motor (de
  jsdelivr y cdnjs) y los guarda; hace falta internet esa vez. El texto se dice tal cual se escribe (no
  se corrige nada solo). El código está en `static/piper/` (`voz.js`, y `fonemas.js`, de
  `@diffusionstudio/piper-wasm`, MIT).
  **Dónde se escucha:** en la sala del parlante, en el parlante (con la música más baja). En todas
  las salas, además, por **📻 radio**: quien la tiene prendida oye el anuncio entrando a la par
  (un segundo de diferencia) y su música baja mientras tanto. El micrófono por radio llega con
  un par de segundos de demora (Chrome, Edge y Firefox; Safari no); si prendés la radio con el
  micrófono ya abierto, puede tardar en engancharse. Las salas sin parlante sólo se oyen por
  radio. Quien está hablando no se oye a sí mismo por la radio de su página. Ojo: con el micrófono
  en la misma sala del parlante puede haber acople (usá auriculares, o mandalo desde otro lugar).

## Salas, radio y chat

- **📻 Radio, con auriculares:** arriba a la derecha (en el celular, en el menú de abajo), **📻** hace sonar en ese dispositivo lo
  que suena en la rockola, a la par (la letra queda en sincro): pausa, ⏭ y ⏮ los sigue sola. Cada uno
  con su volumen, así que si en la oficina prefieren silencio, con el PIN bajen la rockola a 0 y la
  música sigue en los auriculares. Se apaga tocándolo de nuevo, o con la tecla ⏸ del teclado o de los
  auriculares. Es del modo mpv, y necesita un navegador que reproduzca WebM/Opus (el formato en que
  vienen casi todos los temas), como Chrome, Edge o Firefox.
- **🚪 Salas:** arriba, **sala:** elige en cuál estás (🔊 la principal, la del parlante; 📻 las
  otras). Con **＋ nueva sala…** se crea una, con un nombre y un PIN (en un cuadro, encima de lo que
  se esté mirando: la página va a lo mismo en la sala nueva): quien la crea queda
  controlándola (pausa, ⏭, 🔀, 🗑 borrarla); el PIN de la rockola vale en todas. Una sala sin
  parlante **suena mientras alguien la escucha** con 📻 radio: si nadie la escucha, queda en espera
  donde iba, así lo que piden no se pierde sonando para nadie. Para escucharla en una sala de
  reuniones, alcanza con una compu con 📻 prendida y su parlante. El link de una sala es
  `http://<ip>:8000/?sala=ventas` (y `/pantalla?sala=ventas`, para una tele). Hay hasta 8 salas;
  al borrar una se pierden su cola, su chat, sus ♫ playlists y su 📊 recap, y sus canciones quedan
  bajadas por si otra sala las quiere.
- **💬 Chat:** el de la sala en la que estás (en la compu, a la izquierda, debajo de la letra).
  Escribís y Enter. Se borra cuando se reinicia la rockola. Entre los mensajes va, más apagado,
  **el registro** de lo que se hizo con la música y quién fue: quién votó saltear una canción
  (y cuando se salteó por los votos), quién reaccionó (una vez por persona, reacción y canción) y
  quién la cambió directamente con el PIN (**⏭**, **⏮** o **▶ ahora**). Va con el nombre que
  cada uno puso arriba: sin nombre no se vota ni se reacciona (y un ⏭ o ⏮ con el PIN sin
  nombre figura como «Alguien»). Lo que se hace desde la Raspberry (la botonera, el teclado)
  figura como «desde la rockola».
- **📺 Salas de tv:** en el mismo menú de **sala:** están las de tv, y **＋ nueva sala de tv…**
  crea una (con un nombre y un PIN, como las otras). Son otra página: `http://<ip>:8000/tv?sala=almuerzo`
  (`/tv` a secas las lista). Ahí se busca un video de YouTube (cualquiera, no sólo música) o se pega
  su link, y **+ agregar** lo pone en la cola de la sala: hasta `max_por_persona` esperando por
  persona, y cada uno quita los suyos. Para verlo, **▶ mirar acá**: cada uno lo ve en su navegador,
  con su volumen, en el reproductor de YouTube (pantalla completa, subtítulos y calidad, los de
  siempre), y todos van por el mismo segundo.
  - **El control es de todos:** si alguien pausa, le da play o mueve el video (en el video mismo
    o con **⏸ pausa** y **⏭ siguiente**, abajo), les pasa a todos, y dice quién fue. Con el PIN
    de la sala (o el de la rockola), **🔒** suma lo que no es de todos: quitar (✕) o
    adelantar (⤒) videos de otro y **🗑 borrar sala**.
  - **Corre mientras alguien mira:** cada video arranca cuando el primero lo tiene andando (nadie
    se pierde el principio por lo que tarde en cargar), y si se van todos queda en espera, donde
    iba. El que entra tarde cae donde van los demás.
  - También transmisiones **en vivo** (ahí no hay segundo que seguir: se comparten la pausa y el play).
  - La publicidad es de cada uno: al que le toca, cuando termina alcanza a los demás (con YouTube
    Premium en ese navegador, no hay). Un video que no deja verse fuera de YouTube se saltea solo.
  - Hay hasta 8, y andan en los dos modos. Quedan guardadas entre reinicios, con su cola y por
    dónde iba el video (el chat no).

## Si se reinicia o se corta internet

- **Si se reinicia:** al volver a arrancar (`systemctl --user restart rockola`, `./iniciar.sh`, o si
  se corta la luz), cada sala sigue donde estaba: lo que sonaba, en el segundo en que iba (si se
  cortó la luz, unos segundos antes), y la cola tal cual, cada tema con quien lo pidió (así cada
  uno puede seguir quitando los suyos). Lo que esperaba para bajarse (**⬇ descargas**) se sigue
  bajando. Si estaba en pausa, queda en pausa. En el reinicio diario de las 6 queda todo como
  estaba, pero en pausa (la oficina está vacía): ⏯ lo sigue. Al prender la Raspberry también, y
  con `azar_al_prender` sigue solo cuando el parlante está listo. El chat se borra.
- **Si se corta internet:** la música sigue, con lo guardado (lo que ya estaba bajado y el 🔀); lo
  que no anda hasta que vuelva es buscar en YouTube y bajar temas nuevos (las descargas quedan
  esperando). Las guardadas se siguen viendo con su tapa (en la página y en la pantalla) y con
  su letra: la rockola tiene una copia de cada una. El **led** al lado de **♫ ROCKOLA** lo muestra, en la página, en `/pantalla` y en la
  terminal: verde con internet; rojo (titilando) sin internet; apagado si todavía no se sabe o
  si esa página no llega a la rockola. Es el internet de la rockola, no el de tu celular: la
  rockola prueba cada 15 segundos conectarse a Google (8.8.8.8, o 1.1.1.1 de Cloudflare; si en
  esa red están bloqueados, a www.google.com), y mientras no hay, cada 5.

## La pantalla, la terminal y el clima

- **En la terminal** (`./tui.sh`):

  | tecla | | tecla | |
  |---|---|---|---|
  | `espacio` | pausa | `↑` `↓` | elegir en la cola |
  | `n` | siguiente | `t` | que suene a continuación |
  | `a` | anterior / desde el principio | `d` | quitar |
  | `+` `-` | volumen | `z` | aleatorio (🔀) |
  | `q` | salir (la música sigue) | `m` | mezclar la cola |
  | `e` | estadísticas (ir y volver) | | |

  **Va y viene sola:** cada 15 segundos cambia entre el reproductor (lo de siempre) y las
  estadísticas: cuánto sonó y el top de canciones, de artistas y de quién pide más. Cada vez
  muestra otro período (hoy, los últimos 7 días, el año), salteando los que no tienen nada. El
  QR queda en su lugar y lo que suena se sigue viendo, en chiquito, con su barra. Cuando arranca
  otro tema vuelve al reproductor, y si tocás una tecla se queda ahí un minuto. `--ciclo 30`
  cambia cada cuánto; `--ciclo 0`, nunca.

  En la cola, la columna **Votos** dice cuántos 👍 tiene cada una (▲ 2), y debajo de lo que suena
  van las reacciones y los votos para saltearla; cuando alguien reacciona, se ve un instante quién.

  Otras opciones: `--tema nord` (o `tokyo-night`, `dracula`…) y `--url http://otra-maquina:8000`.
  Arriba, al lado de **♫ ROCKOLA**, está el led de internet (ver [Si se corta internet](#si-se-reinicia-o-se-corta-internet)) y, a
  la derecha, el clima.
- **En una tele:** `http://<ip>:8000/pantalla` (sólo mirar: lo que suena, la cola, el QR, el led y
  el clima).
- **El clima:** arriba, después del led, cómo está afuera y cuántos grados hace (en la página, el
  detalle sale al pasar el mouse; en la pantalla y en `/pantalla` va con palabras, a la derecha).
  Se actualiza cada 10 minutos. Sin configurar nada es el de donde está la rockola según su IP
  pública, que suele acertarle a la ciudad pero no siempre: si le erra, poné la tuya en
  `rockola.toml` (`clima = "Rosario"`). Con `clima = false` no se muestra.
