"""🧭 Recomendados: artistas, álbumes y canciones que podrían gustarle a una sala, según lo que más
suena en ella (las estadísticas del recap) y lo que más tiene guardado.

Cómo se arman, para cada sala:
  - Las «semillas»: los artistas que más suenan (lo pedido pesa más que lo que eligió 🔀, lo de
    hace poco más que lo de hace meses, y 🔥 ❤️ suman y 😴 resta) y los que tienen más guardadas,
    cada cosa con su parte. Y los temas que más sonaron.
  - De cada artista semilla, YouTube Music da sus artistas parecidos y sus álbumes; de cada tema
    semilla, su «radio» (las relacionadas). Un artista parecido a varios de los de la sala suma
    por cada uno.
  - Artistas: los parecidos que la sala casi no conoce (menos de 3 guardadas o escuchadas).
    Álbumes: los de los artistas de la sala que no tiene, y los de los artistas recomendados.
    Canciones: las de las radios, y las más escuchadas de los artistas recomendados, que no estén
    guardadas ni hayan sonado. Como mucho dos del mismo artista, y de entre las mejores, un poco
    al azar: así cambian de una vez a la otra.

Es caro (unas 30 preguntas a YouTube la primera vez), así que lo hace un hilo aparte, sin apuro,
y cada sala se vuelve a calcular cada `CADA` segundos. Lo que se le pregunta a YouTube de un
artista vale un día y la radio de un tema, medio día: las vueltas de cada 2 horas casi no piden
nada, sólo vuelven a ordenar con lo que sonó y se guardó mientras tanto. Lo último calculado queda
en recomendados.json: después de reiniciar ya está, aunque no haya internet.
"""
import json
import os
import random
import re
import threading
import time
from pathlib import Path

from estadisticas import _junto
from reproductor import _artistas, _primer_artista
from tapas import copia_de

CADA = 2 * 3600            # cada cuánto se vuelven a calcular los de una sala
REINTENTO = 15 * 60        # si no se pudo (sin internet, YouTube no contesta), se prueba de nuevo a los 15 minutos
INTERES = 7 * 86400        # una sala que nadie miró en una semana deja de calcularse (la principal, siempre)
VENCE_ARTISTA = 86400      # lo que se le preguntó a YouTube de un artista (sus parecidos, álbumes y temas) vale un día
VENCE_RADIO = 12 * 3600    # la radio de un tema, medio día
VENCE_CANAL = 30 * 86400   # qué canal es un artista (casi no cambia); el que no se encontró, una semana
VENCE_SIN_CANAL = 7 * 86400
PAUSA = 1.5                # segundos entre preguntas a YouTube (de a una: no apurarlo)
SEMILLAS_ARTISTAS = 8      # de cuántos artistas de la sala se buscan los parecidos y los álbumes
SEMILLAS_TEMAS = 6         # de cuántos temas se pide la radio
NUEVOS_A_FONDO = 5         # de los artistas recomendados, a cuántos se les buscan sus álbumes y temas
CUANTOS = 12               # artistas, álbumes y canciones que se recomiendan
POR_ARTISTA = 2            # álbumes (o canciones) del mismo artista, como mucho
CONOCIDO = 3               # un artista con esto de guardadas o de temas que sonaron ya se conoce: no se recomienda
PARTE_ESCUCHADO = 0.6      # cuánto pesa lo que suena frente a lo guardado (el resto) al elegir las semillas
VARIOS = {"variousartists", "variosartistas", "va", "varios", "soundtrack"}   # no son un artista

# Recopilaciones: no se recomiendan (repiten lo que ya hay). Los discos en vivo, sí, pero menos.
RECOPILACION = re.compile(r"\b(grandes [eé]xitos|greatest hits|best of|lo mejor de|lo esencial|essentials?|"
                          r"colecci[oó]n|collection|antolog[ií]a|anthology|obras cumbres|hits|20th century masters|"
                          r"soundtrack|banda sonora|motion picture)\b", re.I)
EN_VIVO = re.compile(r"\b(live|en vivo|ao vivo|en directo|unplugged|en concierto|in concert)\b", re.I)
MALAS = re.compile(r"\b(live|en vivo|karaoke|cover|tribute|instrumental|reaction|lyrics?|letra)\b", re.I)
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
ARTISTA_ID = re.compile(r"UC[A-Za-z0-9_-]{10,40}")
ALBUM_ID = re.compile(r"MPRE[A-Za-z0-9_-]{4,60}")
ENTRE_PARENTESIS = re.compile(r"\s*[(\[][^)\]]*[)\]]")


def _chica(url):
    """La misma tapa, de 120 px (ver tapas.copia_de): las listas la muestran chiquita, y la de 544 pesa diez veces más."""
    c = copia_de(url)
    return c[1] if c else str(url or "")


def _tapa(thumbs):
    urls = [t.get("url") for t in thumbs or [] if isinstance(t, dict) and t.get("url")]
    return _chica(urls[-1]) if urls else ""


def clave_tema(titulo, artista):
    """Para no recomendar otra versión de un tema que ya está: «De Música Ligera (Remasterizado 2007)»
    y «De Música Ligera» de Soda Stereo son el mismo."""
    return f"{_junto(ENTRE_PARENTESIS.sub('', str(titulo or '')))}\0{_junto(_primer_artista(artista))}"


def clave_album(titulo, artista):
    return f"{_junto(titulo)}\0{_junto(_primer_artista(artista))}"


def pagina_de_artista(a, bid):
    """Lo que sirve de `get_artist` (ytmusicapi): nombre, tapa, sus parecidos, sus álbumes (los últimos
    que sacó), sus temas más escuchados (sin la duración: YouTube no la da ahí) y los álbumes de esos
    temas (`popular`: los discos que más se escuchan, que no siempre están entre los últimos)."""
    nombre = str(a.get("name") or "").strip()[:120]
    seccion = lambda k: ((a.get(k) or {}).get("results") or []) if isinstance(a.get(k), dict) else []
    parecidos = [{"browseId": r["browseId"], "name": str(r.get("title") or "").strip()[:120],
                  "thumb": _tapa(r.get("thumbnails")), "subs": str(r.get("subscribers") or "")[:20]}
                 for r in seccion("related") if ARTISTA_ID.fullmatch(str(r.get("browseId") or "")) and r.get("title")]
    albumes = [{"browseId": r["browseId"], "title": str(r.get("title") or "").strip()[:120], "artist": nombre,
                "year": str(r.get("year") or "")[:4], "type": "EP" if str(r.get("type") or "").casefold() == "ep" else "Álbum",
                "thumb": _tapa(r.get("thumbnails"))}
               for r in seccion("albums") if ALBUM_ID.fullmatch(str(r.get("browseId") or "")) and r.get("title")]
    por_id = {x["browseId"]: x for x in albumes}
    singles = {str(r.get("browseId") or "") for r in seccion("singles")}
    temas, populares = [], {}
    for t in seccion("songs"):
        vid = str(t.get("videoId") or "")
        if not VIDEO_ID.fullmatch(vid) or t.get("isAvailable") is False or MALAS.search(str(t.get("title") or "")):
            continue
        album = t.get("album")
        aid, titulo = (str(album.get("id") or ""), str(album.get("name") or "").strip()) if isinstance(album, dict) else ("", "")
        if ALBUM_ID.fullmatch(aid) and titulo and aid not in singles:   # (de un single no: es el mismo tema)
            populares.setdefault(aid, por_id.get(aid) or {"browseId": aid, "title": titulo[:120], "artist": nombre,
                                                          "year": "", "type": "Álbum", "thumb": _tapa(t.get("thumbnails"))})
        temas.append({"videoId": vid, "title": str(t.get("title") or "")[:200],
                      "artist": ", ".join(x["name"] for x in t.get("artists") or [] if x.get("name")) or nombre,
                      "duration": 0, "thumb": _tapa(t.get("thumbnails")),
                      "album": str((album.get("name") if isinstance(album, dict) else album) or "")[:200]})
    return {"browseId": bid, "name": nombre, "thumb": _tapa(a.get("thumbnails")),
            "subs": str(a.get("subscribers") or "")[:20], "related": parecidos, "albums": albumes, "songs": temas,
            "popular": list(populares.values())}


def discos_de(pag):
    """Los álbumes de un artista, primero los más escuchados."""
    populares = pag.get("popular") or []
    ya = {x["browseId"] for x in populares}
    return populares + [x for x in pag["albums"] if x["browseId"] not in ya]


def _elegir(candidatas, n, rng, por_artista=None):
    """Las `n` mejores, con un poco de azar: de las 2n de más puntaje (como mucho `por_artista` de cada
    artista), `n` sorteadas según su puntaje, ordenadas de más a menos."""
    pozo, de_cada = [], {}
    for c in sorted(candidatas.values(), key=lambda c: -c["score"]):
        if por_artista:
            k = _junto(_primer_artista(c.get("artist")))
            if de_cada.get(k, 0) >= por_artista:
                continue
            de_cada[k] = de_cada.get(k, 0) + 1
        pozo.append(c)
        if len(pozo) >= 2 * n:
            break
    # (Efraimidis-Spirakis: sortear sin reponer, con probabilidad según el puntaje)
    sorteo = sorted(pozo, key=lambda c: rng.random() ** (1 / max(c["score"], 1e-9)), reverse=True)[:n]
    return sorted(sorteo, key=lambda c: -c["score"])


def _por_que(c):
    """De qué salió (las dos semillas que más aportaron, del tipo de la que más): {"kind", "like": [...]}."""
    orden = sorted(c["_por"].items(), key=lambda kv: -kv[1])
    tipo = orden[0][0][0]
    return {"kind": tipo, "like": [nombre for (t, nombre), _ in orden if t == tipo][:2]}


def _limpio(c, *claves):
    return dict({k: c.get(k) for k in claves}, why=_por_que(c))


class Recomendados:
    """Los recomendados de cada sala. `de(sala)` contesta al toque con lo último calculado (y, si ya
    venció o no hay, le avisa al hilo); el hilo los calcula de a una sala, sin apuro.

    - `datos_de(sala)` -> {"temas": [...], "guardadas": [...]}: lo que sonó (ver
      Estadisticas.gustos) y lo guardado (Descargas.todas).
    - `yt`: con `search` y `get_artist`, como YTMusic.
    - `radio(videoId)` -> las relacionadas con ese tema, como las de la búsqueda.
    - `salas()` -> los ids de las salas que existen.
    """

    def __init__(self, ruta, datos_de, yt, radio, salas, hay_red=lambda: True, principal="principal",
                 espera=60, pausa=PAUSA):
        self.ruta = Path(ruta)
        self.datos_de, self.yt, self.radio, self.salas, self.hay_red = datos_de, yt, radio, salas, hay_red
        self.principal, self.espera, self.pausa = principal, espera, pausa
        self.lock = threading.Lock()
        self.hechos = {}      # sala -> lo último calculado
        self.interes = {}     # sala -> cuándo la miró alguien por última vez
        self.fallo = {}       # sala -> cuándo no se pudo
        self.calculando = None
        self.canales = {}     # clave del nombre -> [cuándo, browseId o None]
        self.artistas = {}    # browseId -> [cuándo, pagina_de_artista]
        self.radios = {}      # videoId -> [cuándo, temas]
        self._despertar = threading.Event()
        self._cerrando = threading.Event()
        self._leer()

    def arrancar(self):
        threading.Thread(target=self._bucle, daemon=True, name="recomendados").start()

    def cerrar(self):
        self._cerrando.set()
        self._despertar.set()

    # --- lo que se guarda

    def _leer(self):
        try:
            doc = json.loads(self.ruta.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(doc, dict):
            return
        cajas = (("salas", self.hechos, dict), ("canales", self.canales, list), ("artistas", self.artistas, list),
                 ("radios", self.radios, list))
        for nombre, caja, tipo in cajas:
            caja.update({k: v for k, v in (doc.get(nombre) or {}).items() if isinstance(v, tipo)})

    def _guardar(self):
        ahora = time.time()
        with self.lock:   # (lo que venció hace rato no se guarda)
            doc = {"salas": self.hechos,
                   "canales": {k: v for k, v in self.canales.items() if ahora - v[0] < VENCE_CANAL},
                   "artistas": {k: v for k, v in self.artistas.items() if ahora - v[0] < 2 * VENCE_ARTISTA},
                   "radios": {k: v for k, v in self.radios.items() if ahora - v[0] < VENCE_RADIO}}
            texto = json.dumps(doc, ensure_ascii=False)
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.ruta.with_suffix(".tmp")
            tmp.write_text(texto)
            os.replace(tmp, self.ruta)
        except OSError as e:
            print(f"  ⚠ no pude guardar {self.ruta}: {e}", flush=True)

    # --- lo que se le pregunta a YouTube (de a uno, y lo que ya se sabe no se pregunta)

    def _pedir(self, caja, clave, vence, pedir, errores=None, pausa=True):
        """Lo de `caja[clave]` si no venció; si no, `pedir()` (y queda anotado). Si no se puede (sin
        red): con `errores`, se anota ahí y vale lo viejo (o None); sin, la excepción sigue."""
        ahora = time.time()
        with self.lock:
            hay = caja.get(clave)
        if hay and ahora - hay[0] < vence(hay[1]):
            return hay[1]
        try:
            valor = pedir()
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            valor = None   # ytmusicapi no entendió la respuesta: no hay (y no se insiste hasta que venza)
        except Exception as e:   # sin red, YouTube no contesta: se vuelve a probar la próxima vez
            if errores is None:
                raise
            errores.append(e)
            return hay[1] if hay else None
        with self.lock:
            caja[clave] = [ahora, valor]
        if pausa:
            self._cerrando.wait(self.pausa)
        return valor

    def _canal(self, nombre, errores):
        def buscar():
            buscado = _junto(nombre)
            for r in self.yt.search(nombre, filter="artists", limit=5) or []:
                if _junto(r.get("artist") or r.get("title")) == buscado and ARTISTA_ID.fullmatch(str(r.get("browseId") or "")):
                    return r["browseId"]
            return None   # (sólo si el nombre coincide: un video suelto trae de «artista» el canal que lo subió)
        return self._pedir(self.canales, _junto(nombre), lambda v: VENCE_CANAL if v else VENCE_SIN_CANAL, buscar, errores)

    def artista(self, bid, errores=None, pausa=False):
        """La página de un artista de YouTube Music (ver pagina_de_artista), de lo ya sabido si es de hoy."""
        return self._pedir(self.artistas, bid, lambda _: VENCE_ARTISTA,
                           lambda: pagina_de_artista(self.yt.get_artist(bid), bid), errores, pausa)

    def _radio(self, vid, errores):
        return self._pedir(self.radios, vid, lambda _: VENCE_RADIO, lambda: [dict(t, thumb=_chica(t.get("thumb"))) for t in self.radio(vid)], errores)

    # --- para la página

    def de(self, sala):
        """Lo último calculado para `sala` (sin nada si todavía no hay): {"at", "computing", "every",
        "artists", "albums", "songs", "seeds", "empty"}. Si venció, se calcula de nuevo (lo hace el hilo)."""
        ahora = time.time()
        with self.lock:
            self.interes[sala] = ahora
            r = self.hechos.get(sala)
            vencido = not r or ahora - r["at"] >= CADA
            reintento = ahora - self.fallo.get(sala, 0) < REINTENTO
            calculando = self.calculando == sala or (vencido and not reintento)
            r = json.loads(json.dumps(r)) if r else {}   # (una copia: quien llama le agrega cosas)
        if vencido:
            self._despertar.set()
        return dict({"at": None, "artists": [], "albums": [], "songs": [], "seeds": {"artists": [], "songs": []},
                     "empty": False}, **r, computing=calculando, every=CADA)

    # --- el hilo

    def _que_toca(self):
        """La sala que hay que calcular ahora (primero las que no tienen nada), o None; y en cuántos
        segundos vence la próxima."""
        ahora, vivas = time.time(), set(self.salas())
        con, proxima = [], CADA
        with self.lock:
            for s in [s for s in self.hechos if s not in vivas]:   # (las que se borraron)
                del self.hechos[s]
            for s in vivas:
                if s != self.principal and ahora - self.interes.get(s, 0) > INTERES:
                    continue
                r = self.hechos.get(s)
                falta = CADA - (ahora - r["at"]) if r else 0
                falta = max(falta, REINTENTO - (ahora - self.fallo.get(s, 0)))
                if falta > 0:
                    proxima = min(proxima, falta)
                    continue
                con.append((r is not None, s != self.principal, s))
        return (min(con)[2] if con else None), proxima

    def _bucle(self):
        self._cerrando.wait(self.espera)   # (que primero arranque lo demás)
        while not self._cerrando.is_set():
            sala, proxima = self._que_toca() if self.hay_red() else (None, 60)
            if sala is None:
                self._despertar.wait(timeout=max(5, min(proxima, 600)))
                self._despertar.clear()
                continue
            with self.lock:
                self.calculando = sala
            try:
                r = self.calcular(sala)
            except Exception as e:
                print(f"  ⚠ 🧭 no pude armar los recomendados de la sala {sala} (pruebo más tarde): {e!r}", flush=True)
                with self.lock:
                    self.fallo[sala] = time.time()
            else:
                with self.lock:
                    self.hechos[sala] = r
                    self.fallo.pop(sala, None)
                self._guardar()
            finally:
                with self.lock:
                    self.calculando = None

    def calcular(self, sala, rng=None):
        """Los recomendados de `sala`, ya (pide por la red lo que no se sepa: puede tardar un minuto)."""
        rng = rng or random.Random()
        d = self.datos_de(sala)
        temas, guardadas = d.get("temas") or [], d.get("guardadas") or []
        vacio = {"at": time.time(), "artists": [], "albums": [], "songs": [], "seeds": {"artists": [], "songs": []}}
        if not temas and not guardadas:
            return dict(vacio, empty=True)

        # Lo que la sala ya conoce: no se recomienda.
        todo = temas + guardadas
        ids_conocidos = {x["videoId"] for x in todo}
        temas_conocidos = {clave_tema(x.get("title"), x.get("artist")) for x in todo}
        albumes_conocidos = {clave_album(x["album"], x.get("artist")) for x in todo if x.get("album")}
        guardado, escuchado, sonaron, nombres = {}, {}, {}, {}
        for g in guardadas:
            for a in _artistas(g.get("artist")):
                k = _junto(a)
                guardado[k] = guardado.get(k, 0) + 1
                nombres.setdefault(k, a)
        for t in temas:
            for a in _artistas(t.get("artist")):
                k = _junto(a)
                escuchado[k] = escuchado.get(k, 0) + max(0.0, t.get("peso") or 0)
                sonaron[k] = sonaron.get(k, 0) + (1 if t.get("veces") else 0)
                nombres.setdefault(k, a)
        conocido = lambda k: guardado.get(k, 0) >= CONOCIDO or sonaron.get(k, 0) >= CONOCIDO

        # Las semillas: lo que más suena y lo que más hay guardado, cada cosa con su parte.
        total_e, total_g = sum(escuchado.values()), sum(guardado.values())
        pe = PARTE_ESCUCHADO if total_e and total_g else 1.0 if total_e else 0.0
        peso = {k: pe * escuchado.get(k, 0) / (total_e or 1) + (1 - pe) * guardado.get(k, 0) / (total_g or 1)
                for k in nombres if k and k not in VARIOS}
        errores, semillas = [], []
        for k in sorted(peso, key=lambda k: (-peso[k], k))[:SEMILLAS_ARTISTAS + 4]:
            if len(semillas) == SEMILLAS_ARTISTAS or peso[k] <= 0:
                break
            bid = self._canal(nombres[k], errores)
            if bid:
                semillas.append((k, bid, peso[k]))
        de_semillas = sum(p for *_, p in semillas) or 1
        claves_semilla = {k for k, *_ in semillas}

        artistas, albumes, canciones = {}, {}, {}

        def sumar(caja, clave, item, puntos, por):
            c = caja.setdefault(clave, dict(item, score=0.0, _por={}))
            c["score"] += puntos
            c["_por"][por] = c["_por"].get(por, 0) + puntos

        def album_bueno(al, artista):
            return not RECOPILACION.search(al["title"]) and clave_album(al["title"], artista) not in albumes_conocidos

        def tema_bueno(t):
            return t["videoId"] not in ids_conocidos and clave_tema(t.get("title"), t.get("artist")) not in temas_conocidos

        vistas = 0   # páginas de artistas y radios que se pudieron ver
        for k, bid, p in semillas:
            pag = self.artista(bid, errores, pausa=True)
            if not pag:
                continue
            vistas += 1
            w = p / de_semillas
            for i, r in enumerate(pag["related"]):
                rk = _junto(r["name"])
                if rk and rk not in claves_semilla and rk not in VARIOS and not conocido(rk):
                    sumar(artistas, r["browseId"], r, w / (1 + 0.25 * i), ("como", nombres[k]))
            for i, al in enumerate(a for a in discos_de(pag) if album_bueno(a, pag["name"])):
                vivo = 0.4 if EN_VIVO.search(al["title"]) else 1.0
                sumar(albumes, al["browseId"], al, 0.7 * w * vivo / (1 + 0.3 * i), ("tuyo", pag["name"] or nombres[k]))

        elegidos = _elegir(artistas, CUANTOS, rng)
        for a in elegidos[:NUEVOS_A_FONDO]:   # de los recomendados: sus álbumes y sus temas
            pag = self.artista(a["browseId"], errores, pausa=True)
            if not pag:
                continue
            vistas += 1
            como = _por_que(a)["like"][:1]
            for i, al in enumerate([x for x in discos_de(pag) if album_bueno(x, pag["name"]) and not EN_VIVO.search(x["title"])][:2]):
                sumar(albumes, al["browseId"], al, 0.6 * a["score"] / (1 + 0.5 * i), ("como", como[0] if como else pag["name"]))
            for i, t in enumerate([x for x in pag["songs"] if tema_bueno(x)][:2]):
                sumar(canciones, t["videoId"], t, 0.5 * a["score"] / (1 + 0.5 * i), ("de", pag["name"]))

        # Los temas semilla: los que más sonaron (y, si son pocos, las guardadas que sonaron más recién).
        sem_temas = sorted((t for t in temas if (t.get("peso") or 0) > 0), key=lambda t: -t["peso"])[:SEMILLAS_TEMAS]
        if len(sem_temas) < SEMILLAS_TEMAS:
            ya = {t["videoId"] for t in sem_temas}
            minimo = min((t["peso"] for t in sem_temas), default=1.0)
            sem_temas += [dict(g, peso=minimo / 2) for g in sorted(guardadas, key=lambda g: -(g.get("usada") or 0))
                          if g["videoId"] not in ya][:SEMILLAS_TEMAS - len(sem_temas)]
        de_temas = sum(t["peso"] for t in sem_temas) or 1
        for s in sem_temas:
            radio = self._radio(s["videoId"], errores)
            if radio is None:
                continue
            vistas += 1
            for i, t in enumerate(radio):
                if tema_bueno(t):
                    sumar(canciones, t["videoId"], t, (s["peso"] / de_temas) / (1 + 0.08 * i), ("tema", s.get("title") or ""))

        if errores and not vistas:   # no se pudo ver nada: que se pruebe más tarde (y quede lo anterior)
            raise errores[0]
        return dict(vacio, empty=False, seeds={
            "artists": [nombres[k] for k, *_ in semillas][:5], "songs": [t.get("title") or "" for t in sem_temas][:3]},
            artists=[_limpio(a, "browseId", "name", "thumb", "subs") for a in elegidos],
            albums=[_limpio(a, "browseId", "title", "artist", "year", "type", "thumb")
                    for a in _elegir(albumes, CUANTOS, rng, POR_ARTISTA)],
            songs=[_limpio(c, "videoId", "title", "artist", "album", "duration", "thumb")
                   for c in _elegir(canciones, CUANTOS, rng, POR_ARTISTA)])
