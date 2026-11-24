"""Motor de reproducción "headless": yt-dlp descarga, mpv reproduce.

- `Descargas`: baja el audio de cada canción con yt-dlp y lo guarda en una carpeta.
  Mientras suena una, ya se va bajando la siguiente, así no hay silencio entre temas.
  Las canciones quedan guardadas (hasta un límite de espacio) para volver a sonar sin
  bajarlas de nuevo, y para el modo aleatorio.
- `Mpv`: maneja un proceso de mpv por su socket JSON IPC (pausa, volumen, posición, fin de tema).

La lógica de "qué tiene que sonar" vive en server.py (el Director): este módulo
sólo sabe descargar y reproducir.
"""
import itertools
import json
import os
import random
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

GB = 1024 ** 3
LIBRE_MINIMO = 2 * GB   # siempre dejar al menos esto libre en el disco
DATOS = ("title", "artist", "duration", "thumb", "album")   # lo que se guarda de cada canción


# ---------------------------------------------------------------- descargas

class ErrorDescarga(Exception):
    pass


class _Mudo:
    """Logger para yt-dlp: los errores igual llegan como excepción."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


class _Trabajo:
    def __init__(self, datos=None):
        self.listo = threading.Event()
        self.ruta = None
        self.error = None
        self.datos = datos   # título, artista, etc. (para guardarla)


def _pide_edad(error):
    """¿Es el error de yt-dlp para un video con restricción de edad? («Sign in to confirm your
    age»: YouTube lo da sólo con una cuenta de un mayor.)"""
    texto = str(error).lower()
    return "confirm your age" in texto or "age-restricted" in texto or "inappropriate for some users" in texto


def _para_buscar(texto):
    """En minúsculas y sin acentos: «Pajaros» encuentra «Pájaros»."""
    t = unicodedata.normalize("NFKD", str(texto or "").casefold())
    return "".join(c for c in t if not unicodedata.combining(c))


def _datos_de_ytdlp(info):
    """Lo que trae yt-dlp: para completar lo que no vino con el pedido (p. ej. el álbum de
    una pedida por link)."""
    artista = ", ".join(info.get("artists") or []) or info.get("uploader") or ""
    return {"title": info.get("track") or info.get("title") or "",
            "artist": artista.removesuffix(" - Topic"),
            "duration": int(info.get("duration") or 0),
            "thumb": info.get("thumbnail") or "",
            "album": info.get("album") or ""}


def _primer_artista(artista):
    return str(artista or "").split(", ")[0].strip()


def _artistas(artista):
    """«Bizarrap, Quevedo» -> ["Bizarrap", "Quevedo"] (así los junta YouTube Music)."""
    return [a.strip() for a in str(artista or "").split(", ") if a.strip()]


class Descargas:
    """Baja canciones con yt-dlp y las guarda en `carpeta`.

    Con `limite` (en bytes) mayor que 0, las canciones quedan guardadas también entre
    corridas: si las vuelven a pedir suenan sin bajarlas, y el modo aleatorio elige entre
    ellas. Al pasar el límite, o si al disco le queda poco lugar, se borran las que hace
    más tiempo que no suenan.

    Cada canción es su audio (`<id>.webm`, `.m4a`…) más `<id>.json` con sus datos. La
    fecha de modificación del audio es la última vez que sonó: no hay un índice que
    se pueda romper si se corta la luz.

    Salas: cada guardada anota en qué salas sonó o se guardó (`salas` en su .json), y la
    lista, el modo aleatorio y la búsqueda de cada sala usan sólo las suyas. El archivo es
    uno solo: si otra sala pide una que ya está bajada, suena sin bajarla y pasa a ser
    también de esa sala. Las que se guardaron antes de que hubiera salas son de `sala_vieja`.

    Con `limite` = 0 sólo quedan las que están en uso, como en una carpeta temporal.

    `pedir` baja una canción ya, en paralelo; `anticipar` baja las que vienen en la cola,
    de a una, para no cargar la conexión ni a YouTube.

    `al_guardar()` se llama (sin ningún lock tomado) cada vez que queda guardada una nueva.
    """

    def __init__(self, carpeta=None, limite=0, al_guardar=None, sala_vieja="principal", otra_version=None):
        self.carpeta = Path(carpeta or Path(tempfile.gettempdir()) / "rockola")
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.limite = max(0, int(limite))
        self.sala_vieja = sala_vieja
        self.lock = threading.Lock()
        self.trabajos = {}   # videoId -> _Trabajo
        self.guardadas = {}  # videoId -> {"ruta", "bytes", "usada", "datos", "salas"}
        self.total = 0       # bytes de las guardadas
        self.pendientes = [] # [(videoId, datos)] que `anticipar` tiene que ir bajando
        self.anticipando = False
        self.sin_lugar = False   # `anticipar` frenó porque el disco está casi lleno
        self.version = 0     # sube cuando cambian las guardadas (para refrescar la lista)
        self.al_guardar = al_guardar
        self.otra_version = otra_version   # otra_version(video_id, datos) -> otro id con el mismo tema (ver _bajar)
        self._leer_carpeta()

    def _leer_carpeta(self):
        """Al arrancar: retoma las canciones guardadas y borra los restos (descargas a medias)."""
        quedan = set()
        if self.limite:
            for js in self.carpeta.glob("*.json"):
                try:
                    datos = json.loads(js.read_text())
                    audio = self.carpeta / Path(datos["archivo"]).name
                    st = audio.stat()
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    continue
                if audio.suffix in (".part", ".json", ".tmp", ".ytdl"):
                    continue
                if datos.get("borrar"):   # la borraron a mano mientras sonaba: ahora sí
                    continue
                self._registrar(js.stem, audio, datos, st.st_size, st.st_mtime)
                quedan |= {js.name, audio.name}
        for f in self.carpeta.iterdir():
            if f.is_file() and f.name not in quedan:
                f.unlink(missing_ok=True)

    def _registrar(self, video_id, audio, datos, tam, usada):
        """Anota una canción guardada. Llamar con el lock tomado (o al arrancar)."""
        viejo = self.guardadas.pop(video_id, None)
        if viejo:
            self.total -= viejo["bytes"]
        salas = datos.get("salas")
        salas = {str(s) for s in salas} if isinstance(salas, list) else {self.sala_vieja}
        t = _Trabajo(datos)
        t.ruta = str(audio)
        t.listo.set()
        self.trabajos[video_id] = t
        self.guardadas[video_id] = {"ruta": str(audio), "bytes": tam, "usada": usada,
                                    "datos": {k: datos.get(k) for k in DATOS},
                                    "salas": salas | (viejo["salas"] if viejo else set()),
                                    # guardada por una versión que no anotaba el álbum
                                    "falta_album": "album" not in datos}
        self.total += tam
        self.version += 1

    @staticmethod
    def _json_de(g):
        """Lo que va en el .json de una guardada."""
        js = dict(g["datos"], archivo=Path(g["ruta"]).name, salas=sorted(g["salas"]))
        return dict(js, borrar=True) if g.get("borrar") else js

    def _escribir_datos(self, video_id, datos):
        js = self.carpeta / f"{video_id}.json"
        tmp = js.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(datos, ensure_ascii=False))
            os.replace(tmp, js)       # de una: un corte de luz no deja un .json a medias
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def _guardar(self, video_id, ruta, datos, sala=None):
        """Deja guardada una canción recién bajada: sus datos al lado, en un .json.
        `sala`: la que la pidió (queda entre las suyas)."""
        audio = Path(ruta)
        datos = dict({k: datos.get(k) for k in DATOS}, archivo=audio.name, salas=[sala] if sala else [])
        datos["album"] = datos["album"] or ""   # "" = no tiene (un video suelto)
        try:
            self._escribir_datos(video_id, datos)
            os.utime(audio)           # "usada" = ahora
            tam = audio.stat().st_size
        except OSError as e:          # p. ej. disco lleno: igual suena, sólo no queda guardada
            print(f"  ⚠ no pude guardar la canción {video_id}: {e}", flush=True)
            return
        with self.lock:
            if self.trabajos.get(video_id) is None:   # la borraron mientras tanto
                return
            self._registrar(video_id, audio, datos, tam, time.time())
        if self.al_guardar:
            try:
                self.al_guardar()
            except Exception as e:
                print(f"  ⚠ avisando que se guardó {video_id}: {e}", flush=True)

    def _bajar(self, video_id, trabajo):
        import yt_dlp  # import tardío: el modo navegador no lo necesita
        from yt_dlp.utils import match_filter_func

        opts = {
            "format": "bestaudio/best",
            # (con el id pedido, se baje de donde se baje: ver `bajar`, abajo)
            "outtmpl": str(self.carpeta / f"{video_id}.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 3,
            "fragment_retries": 3,
            # yt-dlp usa el primero que encuentre instalado (Deno viene en el venv).
            "js_runtimes": {"deno": {}, "node": {}, "bun": {}, "quickjs": {}},
            "logger": _Mudo(),
            "match_filter": match_filter_func("!is_live"),  # nada de transmisiones en vivo
            "max_filesize": 60 * 1024 * 1024,
        }
        def bajar(de):
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(f"https://www.youtube.com/watch?v={de}", download=True)

        try:
            try:
                info = bajar(video_id)
            except Exception as e:
                # Un video con restricción de edad no se baja sin una cuenta. Si es de un tema, casi
                # siempre está también el tema de audio (el del disco), que no la tiene: se baja
                # ése, y queda guardado como el que se pidió.
                otra = self._otra(video_id, trabajo.datos) if _pide_edad(e) else None
                if not otra:
                    raise
                print(f"  ↪ “{(trabajo.datos or {}).get('title') or video_id}”: el video pide confirmar la edad; "
                      "bajo la versión de audio", flush=True)
                info = bajar(otra)
            ruta = (info.get("requested_downloads") or [{}])[0].get("filepath")
            if not ruta or not os.path.exists(ruta):
                raise ErrorDescarga("no se pudo bajar (¿transmisión en vivo o archivo enorme?)")
            if self.limite:
                pedido, bajado = trabajo.datos or {}, _datos_de_ytdlp(info)
                self._guardar(video_id, ruta, {k: pedido.get(k) or bajado[k] for k in DATOS},
                              sala=pedido.get("sala"))
            trabajo.ruta = ruta
        except Exception as e:  # yt-dlp tira DownloadError con mensajes largos
            msg = str(e).replace("ERROR: ", "").split("\n")[0]
            if _pide_edad(e):
                msg = "YouTube pide una cuenta para confirmar la edad, y no encontré otra versión del tema"
            trabajo.error = msg[:160] or type(e).__name__
        finally:
            trabajo.listo.set()

    def _otra(self, video_id, datos):
        """Otro video de YouTube con el mismo tema (lo busca quien armó las Descargas), o None."""
        try:
            otra = self.otra_version(video_id, datos or {}) if self.otra_version else None
        except Exception:   # (no encontrarla no es un error nuevo: queda el de la edad)
            otra = None
        return otra if otra and otra != video_id else None

    def pedir(self, video_id, datos=None):
        """Arranca la descarga en segundo plano (si no estaba ya, o guardada).

        `datos`: título, artista, duración y tapa, para guardarla con su nombre (y `sala`:
        la que la pidió).
        """
        with self.lock:
            t = self._trabajo(video_id)
            if t and not (t.listo.is_set() and t.error):  # si falló antes, reintenta
                if datos and t.datos is None:
                    t.datos = datos
                return t
            t = self.trabajos[video_id] = _Trabajo(datos)
        threading.Thread(target=self._bajar, args=(video_id, t), daemon=True,
                         name=f"descarga-{video_id}").start()
        return t

    def _trabajo(self, video_id):
        """Su descarga (hecha o en curso), salvo que alguien haya borrado el archivo:
        entonces hay que bajarla de nuevo. Llamar con el lock tomado."""
        t = self.trabajos.get(video_id)
        if t and t.listo.is_set() and t.ruta and not os.path.exists(t.ruta):
            self._borrar(video_id)
            return None
        return t

    def anticipar(self, canciones):
        """Baja por adelantado, de a una y en orden, las de `canciones` ([(videoId, datos)])
        que falten.

        Cada llamada reemplaza la lista: si la cola cambia (la mezclan, quitan una), sigue
        con la nueva. No reintenta las que fallaron; eso lo hace `pedir` cuando les toca sonar.
        """
        with self.lock:
            self.pendientes = list(canciones)
            if self.anticipando:
                return   # el hilo que ya está bajando va a ver la lista nueva
            self.anticipando = True
        try:
            threading.Thread(target=self._anticipar, daemon=True, name="anticipar").start()
        except Exception:
            with self.lock:
                self.anticipando = False
            raise

    def lleno(self, margen=100 * 1024 ** 2):
        """Si lo que se guardó ya llega al límite de espacio (`limite`), o al disco le queda poco: bajar
        más haría borrar canciones viejas (ver `conservar`)."""
        return bool(self.limite) and (self.total + margen > self.limite or self._poco_lugar())

    def _poco_lugar(self):
        try:
            return shutil.disk_usage(self.carpeta).free < LIBRE_MINIMO
        except OSError:
            return False

    def _anticipar(self):
        t = None
        try:
            while True:
                lleno = self._poco_lugar()
                if lleno and not self.sin_lugar:
                    print(f"  ⚠ queda poco lugar en el disco: dejo de bajar canciones por adelantado",
                          flush=True)
                with self.lock:
                    self.sin_lugar = lleno
                    # Con el disco casi lleno, frena: `conservar` hace lugar y la próxima
                    # llamada a `anticipar` sigue.
                    sig = None if lleno else next(((vid, datos) for vid, datos in self.pendientes
                                                   if self._trabajo(vid) is None), None)
                    if sig is None:
                        self.anticipando = False
                        return
                    vid, datos = sig
                    t = self.trabajos[vid] = _Trabajo(datos)
                self._bajar(vid, t)
        except Exception as e:   # no debería pasar; que la próxima llamada arranque otro hilo
            print(f"  ⚠ bajando por adelantado: {e}", flush=True)
            if t and not t.listo.is_set():   # que nadie se quede esperándola
                t.error = str(e)[:160] or type(e).__name__
                t.listo.set()
            with self.lock:
                self.anticipando = False

    def esperar(self, video_id, sigue_vigente=lambda: True, timeout=120, datos=None):
        """Devuelve la ruta del archivo, bajándolo si hace falta.

        Devuelve None si mientras espera `sigue_vigente()` pasa a ser falso
        (p. ej. la saltearon): no tiene sentido seguir esperándola.
        """
        t = self.pedir(video_id, datos)
        limite = time.monotonic() + timeout
        while not t.listo.wait(0.5):
            if not sigue_vigente():
                return None
            if time.monotonic() > limite:
                raise ErrorDescarga("la descarga tardó demasiado")
        if t.error:
            raise ErrorDescarga(t.error)
        return t.ruta

    def lista(self, video_id):
        t = self.trabajos.get(video_id)
        return bool(t and t.listo.is_set() and t.ruta)

    def ruta(self, video_id):
        """El archivo de una canción ya bajada, o None (si no está o todavía se está bajando)."""
        with self.lock:
            t = self.trabajos.get(video_id)
            ruta = t.ruta if t and t.listo.is_set() and not t.error else None
        return ruta if ruta and os.path.exists(ruta) else None

    def usada(self, video_id, sala=None):
        """Marca que acaba de sonar (en `sala`, que pasa a tenerla entre las suyas): es de las
        últimas que se borran."""
        with self.lock:
            g = self.guardadas.get(video_id)
            if not g:
                return
            g["usada"] = time.time()
            ruta = g["ruta"]
            self.version += 1
            if sala:
                self._sumar(video_id, g, sala)
        try:
            os.utime(ruta)
        except OSError:
            pass

    def _sumar(self, video_id, g, sala):
        """Que una guardada sea también de `sala`. Con el lock tomado. True si no lo era.
        (Si la habían borrado a mano y todavía no se borró porque sonaba, ya no se borra.)"""
        if sala in g["salas"]:
            return False
        g["salas"].add(sala)
        g["borrar"] = False
        self.version += 1
        try:
            self._escribir_datos(video_id, self._json_de(g))
        except OSError as e:
            print(f"  ⚠ no pude anotar la sala de {video_id}: {e}", flush=True)
        return True

    def sumar_a_sala(self, video_id, sala):
        """Una guardada (por otra sala, o recién bajada) pasa a ser también de `sala`.
        True si no lo era; False si ya lo era o si no está guardada."""
        with self.lock:
            g = self.guardadas.get(video_id)
            return bool(g) and self._sumar(video_id, g, sala)

    def en_sala(self, video_id, sala):
        """Si está guardada y es de `sala`."""
        with self.lock:
            g = self.guardadas.get(video_id)
            return bool(g) and sala in g["salas"]

    def borrar_de_sala(self, video_id, sala, en_uso=(), vivas=None):
        """Borrar a mano una guardada de `sala`: deja de ser suya. Si no queda en ninguna otra
        (de `vivas`, las salas que existen), se borra el archivo; si está sonando o en una cola
        (`en_uso`), cuando deje de estarlo (ver `conservar`).

        Devuelve None si no era de `sala`; si no, {"salas": en las que sigue, "pendiente": si
        se borra más tarde}."""
        with self.lock:
            g = self.guardadas.get(video_id)
            if not g or sala not in g["salas"]:
                return None
            g["salas"].discard(sala)
            if vivas is not None:
                g["salas"] &= set(vivas)
            self.version += 1
            quedan, pendiente = set(g["salas"]), False
            if not quedan and video_id not in en_uso:
                self._borrar(video_id)
                return {"salas": quedan, "pendiente": False}
            if not quedan:
                g["borrar"] = pendiente = True
            try:
                self._escribir_datos(video_id, self._json_de(g))
            except OSError as e:
                print(f"  ⚠ no pude anotar las salas de {video_id}: {e}", flush=True)
        return {"salas": quedan, "pendiente": pendiente}

    def descartar(self, video_id, en_uso=()):
        """Una recién bajada que al final nadie quiere guardar (la cancelaron mientras se bajaba):
        si no es de ninguna sala, se borra (si está en uso, cuando deje de estarlo)."""
        with self.lock:
            g = self.guardadas.get(video_id)
            if not g or g["salas"]:
                return
            if video_id not in en_uso:
                self._borrar(video_id)
                return
            g["borrar"] = True
            try:
                self._escribir_datos(video_id, self._json_de(g))
            except OSError:
                pass

    def quitar_sala(self, sala):
        """Borraron una sala: sus guardadas dejan de ser suyas (el archivo queda: si no es de
        ninguna otra, es de las primeras que se borran cuando falta lugar). Devuelve cuántas."""
        n = 0
        with self.lock:
            for vid, g in self.guardadas.items():
                if sala in g["salas"]:
                    g["salas"].discard(sala)
                    n += 1
                    try:
                        self._escribir_datos(vid, self._json_de(g))
                    except OSError as e:
                        print(f"  ⚠ no pude anotar las salas de {vid}: {e}", flush=True)
            if n:
                self.version += 1
        return n

    def _borrar(self, video_id):
        """Borra el audio, sus datos y restos (.part si falló). Con el lock tomado."""
        self.trabajos.pop(video_id, None)
        g = self.guardadas.pop(video_id, None)
        if g:
            self.total -= g["bytes"]
            self.version += 1
        for f in self.carpeta.glob(f"{video_id}.*"):
            f.unlink(missing_ok=True)

    def conservar(self, en_uso):
        """Libera lugar sin tocar `en_uso` (la que suena, las que vienen, la anterior) ni
        las que se están bajando.

        Sin límite, borra todas las demás. Con límite, borra las que borraron a mano mientras
        sonaban y después las que hace más tiempo que no suenan, hasta entrar en el límite y
        dejar libre `LIBRE_MINIMO` en el disco (primero las que no son de ninguna sala: las de
        salas que se borraron).
        """
        with self.lock:
            for vid, t in list(self.trabajos.items()):
                if vid in en_uso or not t.listo.is_set():
                    continue
                if t.error or not self.limite or vid not in self.guardadas or self.guardadas[vid].get("borrar"):
                    self._borrar(vid)
            if not self.limite:
                return
            try:
                libre = shutil.disk_usage(self.carpeta).free
            except OSError:
                libre = LIBRE_MINIMO
            sobra = max(self.total - self.limite, LIBRE_MINIMO - libre)
            if sobra <= 0:
                return
            for vid in sorted(self.guardadas, key=lambda v: (bool(self.guardadas[v]["salas"]),
                                                             self.guardadas[v]["usada"])):
                if sobra <= 0:
                    break
                if vid in en_uso:
                    continue
                sobra -= self.guardadas[vid]["bytes"]
                self._borrar(vid)

    def _de(self, sala):
        """Las guardadas de `sala` (todas, con None), como (videoId, g). Con el lock tomado."""
        return [(vid, g) for vid, g in self.guardadas.items() if sala is None or sala in g["salas"]]

    def al_azar(self, excluir=(), sala=None):
        """Los datos (con videoId) de una canción guardada al azar (de `sala`), o None si no hay.

        Elige entre las que hace más tiempo que no suenan, para no repetir las de recién.
        """
        with self.lock:
            elegibles = sorted((g["usada"], vid) for vid, g in self._de(sala) if vid not in excluir)
            if not elegibles:
                return None
            elegibles = elegibles[:len(elegibles) - min(len(elegibles) // 2, 50)]
            _, vid = random.choice(elegibles)
            return dict(self.guardadas[vid]["datos"], videoId=vid)

    def ids(self, sala=None):
        """Los videoId de las guardadas (con `sala`, las de esa sala)."""
        with self.lock:
            return {vid for vid, _ in self._de(sala)}

    def tapas(self):
        """Las direcciones de las tapas de las guardadas (cada una, una vez)."""
        with self.lock:
            return {g["datos"].get("thumb") for g in self.guardadas.values()} - {None, ""}

    def datos(self, video_id):
        """Los datos (con videoId) de una guardada, o None."""
        with self.lock:
            g = self.guardadas.get(video_id)
            return g and dict(g["datos"], videoId=video_id)

    def listado(self, buscar="", desde=0, cuantas=30, sala=None):
        """Una página de las guardadas (de `sala`), de la que sonó (o se bajó) más recién a la
        más vieja.

        Con `buscar`, sólo las que tienen todas sus palabras en el título, el artista o el
        álbum, en cualquier orden y sin fijarse en mayúsculas ni acentos («soda furia»
        encuentra «En la Ciudad de la Furia», de Soda Stereo).
        """
        palabras = _para_buscar(buscar).split()
        with self.lock:
            orden = sorted(self._de(sala), key=lambda kv: kv[1]["usada"], reverse=True)
            if palabras:
                orden = [(vid, g) for vid, g in orden if self._coincide(g, palabras)]
            pagina = [dict(g["datos"], videoId=vid) for vid, g in orden[desde:desde + cuantas]]
            return {"total": len(orden), "songs": pagina, "v": self.version}

    @staticmethod
    def _coincide(g, palabras):
        d = g["datos"]
        texto = _para_buscar(" ".join(str(d.get(k) or "") for k in ("title", "artist", "album")))
        return all(p in texto for p in palabras)

    def albumes(self, buscar="", desde=0, cuantos=20, sala=None):
        """Una página de las guardadas (de `sala`) agrupadas por álbum, en orden alfabético.

        Un álbum es su nombre más el primer artista (hay muchos «Grandes éxitos»). Las que
        no tienen álbum (videos sueltos) van al final, juntas. Con `buscar`, como `listado`.
        """
        with self.lock:
            grupos, version = self._por_album(sala, _para_buscar(buscar).split()), self.version
        orden = self._en_orden(grupos.values())
        return {"total": len(orden), "songs": sum(len(a["songs"]) for a in orden),
                "albums": orden[desde:desde + cuantos], "v": version}

    def discos(self, buscar="", sala=None):
        """Todos los álbumes guardados (de `sala`), sin sus temas: sólo nombre, artista, tapa, cuántos
        temas tiene guardados y cuánto duran. Es la lista liviana de la biblioteca (⬇ descargas), para
        elegir varios y completarlos; en orden alfabético, y los videos sueltos (sin álbum) no están."""
        with self.lock:
            grupos = self._por_album(sala, _para_buscar(buscar).split())
        orden = [a for a in self._en_orden(grupos.values()) if a["album"]]
        return {"albums": [{"key": a["key"], "album": a["album"], "artist": a["artist"], "thumb": a["thumb"],
                            "count": len(a["songs"]), "duration": a["duration"]} for a in orden],
                "total": len(orden)}

    @staticmethod
    def _clave_album(d):
        """(clave, álbum, artista) del álbum de un tema: "" si es un video suelto."""
        album = str(d.get("album") or "").strip()
        artista = _primer_artista(d.get("artist")) if album else ""
        return f"{_para_buscar(album)}\0{_para_buscar(artista)}", album, artista

    def _por_album(self, sala, palabras=()):
        """Las guardadas (de `sala`) agrupadas por álbum: {clave: álbum, con sus temas}. Con el
        lock tomado."""
        grupos = {}
        for vid, g in self._de(sala):
            if palabras and not self._coincide(g, palabras):
                continue
            d = g["datos"]
            k, album, artista = self._clave_album(d)
            a = grupos.setdefault(k, {"key": k, "album": album, "artist": artista,
                                      "thumb": "", "duration": 0, "songs": []})
            a["songs"].append(dict(d, videoId=vid))
            a["duration"] += int(d.get("duration") or 0)
            a["thumb"] = a["thumb"] or d.get("thumb") or ""
        return grupos

    @staticmethod
    def _en_orden(albumes):
        """Los álbumes por orden alfabético (los temas sin álbum, al final), cada uno con sus temas
        también por orden alfabético."""
        orden = sorted(albumes, key=lambda a: (not a["album"], _para_buscar(a["album"]), _para_buscar(a["artist"])))
        for a in orden:
            a["songs"].sort(key=lambda s: _para_buscar(s.get("title")))
        return orden

    def temas_de_album(self, clave, sala=None):
        """Los temas guardados (de `sala`) del álbum de esa clave, en orden alfabético."""
        with self.lock:
            temas = [dict(g["datos"], videoId=vid) for vid, g in self._de(sala) if self._clave_album(g["datos"])[0] == clave]
        return sorted(temas, key=lambda s: _para_buscar(s.get("title")))

    def ids_de_albumes(self, claves, sala=None):
        """Los videoId de las guardadas (de `sala`) de esos álbumes (sus claves: las de `discos`)."""
        claves = set(claves)
        with self.lock:
            return [vid for vid, g in self._de(sala) if self._clave_album(g["datos"])[0] in claves]

    def del_album(self, album, artista, sala=None):
        """Cuántas guardadas (de `sala`) son de ese álbum: el mismo nombre y primer artista,
        como en `albumes`."""
        k = (_para_buscar(str(album or "").strip()), _para_buscar(_primer_artista(artista)))
        with self.lock:
            return sum((_para_buscar(str(g["datos"].get("album") or "").strip()),
                        _para_buscar(_primer_artista(g["datos"].get("artist")))) == k
                       for _, g in self._de(sala))

    def _por_artista(self, sala):
        """Las guardadas (de `sala`) de cada artista: {clave: artista, con sus temas}. Un tema de
        varios («Bizarrap, Quevedo») está en el de cada uno. La tapa es la del que sonó más
        recién. Con el lock tomado."""
        grupos = {}
        for vid, g in sorted(self._de(sala), key=lambda kv: kv[1]["usada"], reverse=True):
            d = g["datos"]
            for nombre in dict.fromkeys(_artistas(d.get("artist")) or [""]):
                k = " ".join(_para_buscar(nombre).split())
                a = grupos.setdefault(k, {"key": k, "artist": nombre, "thumb": "", "duration": 0, "songs": []})
                a["songs"].append(dict(d, videoId=vid))
                a["duration"] += int(d.get("duration") or 0)
                a["thumb"] = a["thumb"] or d.get("thumb") or ""
        return grupos

    def artistas(self, desde=0, cuantos=20, sala=None):
        """Una página de los artistas de las guardadas (de `sala`), en orden alfabético, con cuántos
        temas y cuántos álbumes tiene cada uno (los temas y los álbumes, con `del_artista`)."""
        with self.lock:
            grupos, version, temas = self._por_artista(sala), self.version, len(self._de(sala))
        orden = sorted(grupos.values(), key=lambda a: (not a["artist"], a["key"]))
        pagina = [dict({k: a[k] for k in ("key", "artist", "thumb", "duration")}, count=len(a["songs"]),
                       albumCount=len({self._clave_album(s)[0] for s in a["songs"] if str(s.get("album") or "").strip()}))
                  for a in orden[desde:desde + cuantos]]
        return {"total": len(orden), "songs": temas, "artists": pagina, "v": version}

    def del_artista(self, clave, sala=None):
        """Lo guardado (en `sala`) de un artista (`clave`: su "key" en `artistas`), o None si no
        hay nada: sus temas (`songs`, por orden alfabético) y los álbumes en los que está
        (`albums`, como los de `albumes`: cada uno con todos sus temas guardados, también los de
        otros artistas). Sus temas sueltos van al final, como un álbum sin nombre."""
        with self.lock:
            a, discos, version = self._por_artista(sala).get(clave), self._por_album(sala), self.version
        if not a:
            return None
        claves, sueltos = {}, []
        for s in a["songs"]:
            k, album, _ = self._clave_album(s)
            if album:
                claves[k] = True
            else:
                sueltos.append(s)
        albumes = self._en_orden([discos[k] for k in claves if k in discos])
        if sueltos:
            albumes += self._en_orden([{"key": "\0", "album": "", "artist": "", "thumb": sueltos[0].get("thumb") or "",
                                        "duration": sum(int(s.get("duration") or 0) for s in sueltos), "songs": sueltos}])
        a["songs"].sort(key=lambda s: _para_buscar(s.get("title")))
        return dict(a, count=len(a["songs"]), albumCount=len(claves), albums=albumes, v=version)

    def autores(self, sala=None):
        """De quién es cada guardada (de `sala`), como viene en el tema («Bizarrap, Quevedo»)."""
        with self.lock:
            return {str(g["datos"].get("artist") or "") for _, g in self._de(sala)}

    def _por_genero(self, de, sala):
        """Las guardadas (de `sala`) agrupadas por género: {clave: grupo, con sus temas}. `de` dice
        el género de cada artista ({artista: género}, como viene en el tema: lo sabe
        Estadisticas.generos_de); las de uno que no está ahí quedan juntas, sin género (""). La
        tapa de cada grupo es la de la que sonó más recién. Con el lock tomado."""
        grupos = {}
        for vid, g in sorted(self._de(sala), key=lambda kv: kv[1]["usada"], reverse=True):
            d = g["datos"]
            genero = de.get(str(d.get("artist") or ""), "")
            k = _para_buscar(genero)
            gr = grupos.setdefault(k, {"key": k, "genre": genero, "thumb": "", "duration": 0, "songs": []})
            gr["songs"].append(dict(d, videoId=vid))
            gr["duration"] += int(d.get("duration") or 0)
            gr["thumb"] = gr["thumb"] or d.get("thumb") or ""
        return grupos

    def generos(self, de, desde=0, cuantos=20, sala=None):
        """Una página de los géneros de las guardadas (de `sala`), en orden alfabético, con cuántos
        temas tiene cada uno (los temas, con `del_genero`). Las que no tienen género van al
        final. `de`: ver `_por_genero`."""
        with self.lock:
            grupos, version = self._por_genero(de, sala), self.version
        orden = sorted(grupos.values(), key=lambda g: (not g["genre"], g["key"]))
        pagina = [dict({k: g[k] for k in ("key", "genre", "thumb", "duration")}, count=len(g["songs"]))
                  for g in orden[desde:desde + cuantos]]
        return {"total": len(orden), "songs": sum(len(g["songs"]) for g in orden), "genres": pagina, "v": version}

    def del_genero(self, de, clave, sala=None):
        """Las guardadas (de `sala`) de un género (`clave`: su "key" en `generos`), por artista y
        álbum, o None si no hay ninguna."""
        with self.lock:
            g, version = self._por_genero(de, sala).get(clave), self.version
        if not g:
            return None
        g["songs"].sort(key=lambda s: tuple(_para_buscar(s.get(k)) for k in ("artist", "album", "title")))
        return dict(g, count=len(g["songs"]), v=version)

    def sin_album(self):
        """Las guardadas por una versión que no anotaba el álbum."""
        with self.lock:
            return [vid for vid, g in self.guardadas.items() if g["falta_album"]]

    def completar_album(self, video_id, album):
        """Anota el álbum de una guardada ("" = no tiene), también en su .json."""
        with self.lock:
            g = self.guardadas.get(video_id)
            if not g:
                return   # la borraron mientras tanto
            g["datos"]["album"] = album or ""
            g["falta_album"] = False
            self.version += 1
            try:
                self._escribir_datos(video_id, self._json_de(g))
            except OSError as e:
                print(f"  ⚠ no pude anotar el álbum de {video_id}: {e}", flush=True)

    def resumen(self, sala=None):
        """Cuántas hay (de `sala`) y el espacio que ocupan todas (el lugar es uno para todas las salas)."""
        with self.lock:
            return {"songs": len(self._de(sala)), "all": len(self.guardadas), "bytes": self.total,
                    "limit": self.limite, "v": self.version}


# ---------------------------------------------------------------------- mpv

def _ruta_socket(numero=0):
    """Ruta para el socket IPC. Los sockets Unix aceptan rutas de ~108 bytes como mucho.
    `numero`: cuál de los mpv es (con el crossfade hay dos, cada uno con su socket)."""
    nombre = f"rockola-mpv-{os.getpid()}{f'-{numero}' if numero else ''}.sock"
    for base in (os.environ.get("XDG_RUNTIME_DIR"), tempfile.gettempdir(), "/tmp"):
        if base and os.path.isdir(base) and len(os.path.join(base, nombre).encode()) < 100:
            return os.path.join(base, nombre)
    return os.path.join("/tmp", nombre)


def _morir_con_el_padre():
    """En Linux, que mpv reciba SIGTERM si el servidor muere (no quedan mpv huérfanos)."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:
        pass


# Ojo con PR_SET_PDEATHSIG: el «padre» es el hilo que creó el proceso, no el programa. Si a mpv lo
# lanza un hilo de paso (el de un pedido de la web que lo encuentra caído, el de un crossfade),
# recibe el SIGTERM apenas ese hilo termina. Por eso se lanza siempre desde este, que no termina
# hasta que se cierra la rockola.
_LANZADOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mpv-lanzador")


def _lanzar(cmd, **opciones):
    """subprocess.Popen, pero desde el hilo que queda (ver arriba)."""
    return _LANZADOR.submit(subprocess.Popen, cmd, **opciones).result()


class ErrorMpv(Exception):
    pass


class Mpv:
    """Un proceso de mpv controlado por JSON IPC.

    `al_terminar(item_id, error)` se llama cuando una canción termina sola
    (error=None) o falla. No se llama cuando la cortamos nosotros.
    `al_cambiar(nombre, valor)` se llama cuando cambian pausa, volumen o duración.

    `al_reiniciar()` se llama si mpv se cayó o se colgó y hubo que levantarlo de nuevo
    (lo que estaba sonando se perdió: el Director lo vuelve a cargar).

    `pedido` es lo que le pedimos que suene (lo usa el Director para decidir).
    `actual` es lo que mpv confirmó que está sonando (lo usan los eventos).

    Casi siempre hay uno solo. Con el crossfade prendido el Director levanta otro (`numero` 1):
    mientras uno termina una canción, el otro arranca la que sigue.
    """

    def __init__(self, al_terminar, al_cambiar, volumen=80, args_extra=(), al_reiniciar=None, numero=0):
        if not shutil.which("mpv"):
            raise ErrorMpv("No encuentro mpv. Instalalo con: sudo apt install mpv")
        self.al_terminar = al_terminar
        self.al_cambiar = al_cambiar
        self.al_reiniciar = al_reiniciar
        self.volumen = volumen        # se actualiza con cada cambio, para no perderlo al reiniciar
        self.lock_vida = threading.RLock()  # un solo hilo a la vez arranca/reinicia mpv
        self.arrancado = False
        self.timeouts = 0
        self.args_extra = list(args_extra)
        self.socket_path = _ruta_socket(numero)
        self.proc = None
        self.sock = None
        self.log = None
        self.lock_envio = threading.Lock()
        self.lock = threading.Lock()  # protege pedido/actual/entrada/finales
        self.ids = itertools.count(1)
        self.pendientes = {}          # request_id -> [Event, respuesta]
        self.pedido = None
        self.actual = None
        self.entrada = None           # playlist_entry_id de mpv para `actual`
        self.finales = {}             # fines que llegaron antes de saber de quién eran
        self.vivo = False

    # --- proceso y socket

    def iniciar(self):
        with self.lock_vida:
            self._iniciar()
            self.arrancado = True

    def _iniciar(self):
        self.cerrar()
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        cmd = ["mpv", "--idle=yes", "--no-video", "--no-config", "--quiet",
               "--input-terminal=no", "--msg-level=all=error",
               "--audio-display=no", "--force-window=no", "--ytdl=no",
               f"--volume={round(self.volumen)}",
               f"--input-ipc-server={self.socket_path}", *self.args_extra]
        # stderr a un archivo (no a un pipe): si nadie lee un pipe, se llena y mpv se traba.
        self.log = tempfile.TemporaryFile()
        self.proc = _lanzar(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=self.log, preexec_fn=_morir_con_el_padre)
        limite = time.time() + 10
        while not os.path.exists(self.socket_path):
            if self.proc.poll() is not None:
                self.log.seek(0)
                err = self.log.read().decode(errors="replace").strip()
                raise ErrorMpv(f"mpv se cerró al arrancar (¿MPV_ARGS?): {err[:300]}")
            if time.time() > limite:
                raise ErrorMpv("mpv no abrió el socket IPC")
            time.sleep(0.05)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)
        self.sock.settimeout(5)  # que un mpv trabado no deje colgado un sendall para siempre
        self.vivo = True
        self.timeouts = 0
        with self.lock:
            self.pedido = self.actual = self.entrada = None
            self.finales.clear()
        threading.Thread(target=self._leer, args=(self.sock,), daemon=True, name="mpv-ipc").start()
        for i, prop in enumerate(["pause", "volume", "duration"], start=1):
            self.comando("observe_property", i, prop)

    def esta_vivo(self):
        p = self.proc
        return self.vivo and p is not None and p.poll() is None

    def asegurar(self):
        """Si mpv se cayó o se colgó, lo levanta de nuevo y avisa."""
        with self.lock_vida:
            if self.esta_vivo():
                return
            era_reinicio = self.arrancado
            self.iniciar()
        if era_reinicio and self.al_reiniciar:
            self.al_reiniciar()

    def _colgado(self):
        """mpv no contesta: lo damos por muerto (el próximo asegurar() lo reinicia)."""
        self.vivo = False
        p = self.proc
        if p and p.poll() is None:
            p.kill()

    def cerrar(self):
        self.vivo = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def _leer(self, sock):
        buf = b""
        try:
            while True:
                try:
                    datos = sock.recv(65536)
                except TimeoutError:
                    continue  # sin novedades: el timeout del socket es para los envíos
                if not datos:
                    break
                buf += datos
                *lineas, buf = buf.split(b"\n")
                for linea in lineas:
                    if linea.strip():
                        self._mensaje(json.loads(linea))
        except (OSError, ValueError):
            pass
        finally:
            if sock is self.sock:
                self.vivo = False
                for ev, _ in list(self.pendientes.values()):
                    ev.set()

    def _mensaje(self, msg):
        if "request_id" in msg and "event" not in msg:
            p = self.pendientes.get(msg["request_id"])
            if p:
                p[1] = msg
                p[0].set()
            return
        ev = msg.get("event")
        if ev == "property-change":
            if msg.get("name") == "volume" and msg.get("data") is not None:
                self.volumen = msg["data"]
            self.al_cambiar(msg.get("name"), msg.get("data"))
        elif ev == "end-file":
            razon = msg.get("reason")
            if razon not in ("eof", "error"):  # "stop" = la cortamos nosotros
                return
            err = None if razon == "eof" else (msg.get("file_error") or "mpv no pudo reproducirla")
            entrada = msg.get("playlist_entry_id")
            with self.lock:
                if self.actual is None or (entrada is not None and entrada != self.entrada):
                    # Puede ser de la que se está cargando y todavía no sabemos su id:
                    # lo guardamos y `reproducir` lo revisa cuando se entera.
                    if entrada is not None:
                        self.finales[entrada] = err
                    return
                item, self.actual = self.actual, None
            # al_terminar sólo toca el estado (es rápido), por eso va en este hilo.
            self.al_terminar(item, err)

    def comando(self, *args, timeout=5):
        if not self.vivo:
            raise ErrorMpv("mpv no está corriendo")
        rid = next(self.ids)
        espera = [threading.Event(), None]
        self.pendientes[rid] = espera
        try:
            datos = json.dumps({"command": list(args), "request_id": rid}).encode() + b"\n"
            with self.lock_envio:
                self.sock.sendall(datos)
            if not espera[0].wait(timeout) or espera[1] is None:
                if self.vivo:
                    self.timeouts += 1
                    if self.timeouts >= 2:
                        self._colgado()
                raise ErrorMpv(f"mpv no respondió a {args[0]}")
            self.timeouts = 0
            resp = espera[1]
            if resp.get("error") != "success":
                raise ErrorMpv(f"{args[0]}: {resp.get('error')}")
            return resp.get("data")
        except OSError as e:
            self.vivo = False
            raise ErrorMpv(str(e))
        finally:
            self.pendientes.pop(rid, None)

    # --- reproducción

    def reproducir(self, item_id, ruta, desde=0, pausada=False):
        """Que suene `ruta`, desde el segundo `desde` (para retomar una que venía sonando). Con
        `pausada`, queda cargada pero en pausa. Devuelve False si terminó antes de arrancar."""
        self.asegurar()
        with self.lock:
            self.pedido = item_id
            self.actual = self.entrada = None  # mientras carga, los eventos van a `finales`
            self.finales.clear()
        try:
            if pausada or desde > 0:   # en pausa hasta llegar adonde iba: que no se oiga el principio
                self.comando("set_property", "pause", True)
            data = self.comando("loadfile", ruta, "replace")
        except ErrorMpv:
            with self.lock:  # no quedó pedida: que el Director pueda reintentar
                self.pedido = None
            raise
        # mpv >= 0.38 devuelve el id de la entrada; en versiones viejas lo preguntamos
        # (con "replace" la lista tiene una sola entrada: la nueva).
        if isinstance(data, dict) and "playlist_entry_id" in data:
            entrada = data["playlist_entry_id"]
        else:
            try:
                entrada = self.comando("get_property", "playlist/0/id")
            except ErrorMpv:
                entrada = None
        with self.lock:
            self.entrada = entrada
            if entrada is not None and entrada in self.finales:
                err = self.finales.pop(entrada)   # terminó (o falló) antes de que llegáramos
                termino = True
            else:
                self.actual = item_id
                termino = False
        if termino:
            self.al_terminar(item_id, err)
            return False
        if desde > 0:
            self._saltar(desde)
        if not pausada:
            self.comando("set_property", "pause", False)
        return True

    def _saltar(self, desde, espera=5):
        """Recién cargada (en pausa): ir al segundo `desde`. Hay que esperar a que mpv la abra (hasta
        entonces no tiene time-pos). Con `seek`, que anda igual en todas las versiones (las opciones
        de loadfile cambiaron de lugar en mpv 0.38). Si no se puede, arranca desde el principio."""
        hasta = time.monotonic() + espera
        try:
            while self.posicion() is None:
                if time.monotonic() > hasta or self.pedido is None:
                    return
                time.sleep(0.05)
            self.comando("seek", desde, "absolute")
        except ErrorMpv as e:
            print(f"  ⚠ no pude retomar en {desde:.0f} s: {e}", flush=True)

    def fijar_pausa(self, pausa):
        self.asegurar()
        self.comando("set_property", "pause", bool(pausa))

    def detener(self):
        with self.lock:
            self.pedido = self.actual = self.entrada = None
        if self.vivo:
            self.comando("stop")

    def posicion(self):
        """Segundos reproducidos, o None si no hay archivo cargado.

        Tira ErrorMpv si mpv no responde (no es lo mismo que "no hay nada cargado").
        """
        try:
            return self.comando("get_property", "time-pos", timeout=2)
        except ErrorMpv as e:
            if "property unavailable" in str(e):
                return None
            raise

    def desde_el_principio(self):
        if self.pedido is not None:
            self.comando("seek", 0, "absolute")

    def alternar_pausa(self):
        self.asegurar()
        self.comando("cycle", "pause")

    def cambiar_volumen(self, delta):
        self.asegurar()
        self.comando("add", "volume", delta)  # mpv lo limita solo a 0..130

    def fijar_volumen(self, valor):
        self.asegurar()
        self.comando("set_property", "volume", max(0.0, min(130.0, float(valor))))
