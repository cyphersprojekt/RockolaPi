#!/usr/bin/env python3
"""Rockola de oficina.

Tus compañeros entran a http://<tu-ip>:8000 y agregan canciones a la cola.

Dos modos de reproducción:
  mpv        la música suena en esta máquina (yt-dlp + mpv). Ideal para una Raspberry.
             Para ver qué suena: `tui.py` en la terminal, o /pantalla en un navegador.
  navegador  la música suena en http://localhost:8000/player (reproductor de YouTube).

Por defecto usa mpv si está instalado. La configuración va en rockola.toml.

Salas (modo mpv): la principal suena en el parlante de esta máquina. Las otras, que se crean
desde la web, no tienen parlante: cada una tiene su cola, su chat y sus guardadas, y se
escuchan con 📻 radio, cada uno en su compu.

Salas de tv (📺, en los dos modos): para mirar videos de YouTube entre varios, cada uno en su
navegador (/tv). No pasan por esta máquina: no se baja nada ni suena en el parlante (ver tele.py).
"""
import argparse
import hashlib
import hmac
import itertools
import json
import os
import random
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
import unicodedata
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from ytmusicapi import YTMusic

from listas import MAX_TEMAS as MAX_TEMAS_LISTA, ErrorLista, Listas
from tele import ErrorTele, Teles

# ------------------------------------------------------------------ ajustes
# Valores por defecto. Se cambian en rockola.toml (ver rockola.ejemplo.toml), no acá.
PORT = 8000
MAX_PER_PERSON = 3        # canciones pendientes por persona en la cola
MAX_DURATION = 10 * 60    # duración máxima en segundos (0 = sin límite)
VOLUME = 80               # volumen inicial en modo mpv (0-130)
NORMALIZAR = False        # True: iguala el volumen entre canciones (filtro de mpv)
MPV_ARGS = []             # opciones extra para mpv
PIN = ""                  # PIN para controlar la música desde la web ("" = sólo desde esta máquina)
IP_FIJA = ""              # IP para el link/QR ("" = detectarla)
MODO = ""                 # "mpv", "navegador" o "" (automático)
GUARDAR_GB = 16           # espacio para las canciones guardadas (0 = no guardar)
CARPETA = ""              # dónde se guardan ("" = ~/.cache/rockola)
PREDESCARGAR = 0          # canciones de la cola que se bajan por adelantado (0 = todas)
AZAR_AL_PRENDER = True    # al prender la Raspberry: 🔀 prendido y a sonar (con el parlante listo)
CONFIG = Path(__file__).parent / "rockola.toml"

STATIC = Path(__file__).parent / "static"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
URL_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")
PLAYLIST_ID = re.compile(r"[?&]list=([A-Za-z0-9_-]{2,100})")
ALBUM_ID = re.compile(r"^MPRE[A-Za-z0-9_-]{4,60}$")   # browseId de un álbum de YouTube Music
MAX_PLAYLIST = 200        # temas que se cargan, como mucho, de una playlist
MAX_ALBUMES = 4           # álbumes que aparecen, como mucho, al buscar
MAX_VIDEOS = 12           # videos que aparecen, como mucho, al buscar para una sala de tv
DATOS_TEMA = ("videoId", "title", "artist", "duration", "thumb", "album")   # los de un tema, sin más
MAX_SUELTOS = 20          # temas sueltos que se suman de una vez a una ♫ playlist
RANGO = re.compile(r"bytes=(\d*)-(\d*)")   # el pedazo de un archivo que pide el navegador
TIPOS_AUDIO = {".webm": "audio/webm", ".weba": "audio/webm", ".opus": "audio/ogg", ".ogg": "audio/ogg",
               ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".mp3": "audio/mpeg", ".aac": "audio/aac"}

# -------------------------------------------------------------------- estado
lock = threading.Lock()
changed = threading.Condition(lock)   # despierta a los que esperan cambios
state = {
    "version": 0,      # sube con cada cambio, en cualquier sala (el Director lo mira)
    "mode": "navegador",
    "volume": None,    # el del parlante (sala principal)
    "saving": [],      # las que pidieron guardar sin ponerlas en la cola (modo mpv), de todas las salas
}
MAX_GUARDANDO = 40     # cuántas pueden estar esperando para bajarse así, entre todos (un álbum son ~12)
PRINCIPAL = "principal"
MAX_SALAS = 8          # contando la principal
MAX_CHAT = 100         # mensajes que se recuerdan por sala (en memoria: al reiniciar, se borran)
OYENTE_VENCE = 40      # segundos sin noticias de una 📻 radio prendida para darla por apagada
                       # (la página pregunta el estado cada 25 s como mucho)


class Sala:
    """Una sala: lo que suena, su cola, su chat. La principal suena en el parlante (mpv lleva
    el tiempo); las otras, sólo por 📻 radio (lo lleva un reloj, ver Director.mover)."""

    def __init__(self, id, nombre, principal=False, pin="", sal=""):
        self.id, self.nombre, self.principal = id, nombre, principal
        self.pin, self.sal = pin, sal      # el PIN de la sala, con sal y hash (la principal usa PIN)
        self.version = 0                   # sube con cada cambio; las páginas esperan a que cambie
        self.now = None                    # canción sonando
        self.queue = []                    # próximas
        self.history = []                  # ya sonaron
        self.progress = {"t": 0, "d": 0, "playing": False}
        self.loading = False               # bajando la que tiene que sonar
        self.notice = None                 # {"id", "text"}: último aviso (p. ej. una canción salteada)
        self.shuffle = False               # con la cola vacía, suenan guardadas al azar (modo mpv)
        self.owners = {}                   # item id -> client id (quién la pidió, para poder quitarla)
        self.chat = []                     # [{"id", "by", "text", "at", "client"}]
        self.oyentes = {}                  # client -> cuándo se lo vio con la 📻 radio prendida
        self.reanudar = None               # {"id", "t", "pausada"}: la que sonaba antes de reiniciar,
                                           # que tiene que seguir en ese segundo (ver retomar_colas)
        # Sólo las que no tienen parlante:
        self.pausada = False               # la pausaron (con el PIN)
        self.trabajo = None                # (item id, descarga) de la que suena
        self.lista = False                 # la que suena ya está bajada y arrancó

    def publica(self):
        return {"id": self.id, "name": self.nombre, "main": self.principal}


salas = {PRINCIPAL: Sala(PRINCIPAL, "Principal", principal=True)}
meta_cache = {}        # videoId -> metadatos (sólo se aceptan canciones conocidas)
playlists = {}         # id -> (cuándo, datos): verla y después cargarla no la baja dos veces
albumes = {}           # browseId -> (cuándo, datos): lo mismo con los álbumes
director = None        # Director en modo mpv
stats = None           # Estadisticas: lo que sonó, para el recap (/recap)
listas = None          # Listas: las ♫ playlists que se arman en la rockola, de cada sala
teles = None           # Teles: las salas de tv (📺), para mirar videos de YouTube entre varios
videos = {}            # videoId -> datos de un video (de lo que se buscó para una sala de tv)
fallos_pin = {}        # ip -> (intentos fallidos, bloqueado hasta)

_local = threading.local()


def cargar_config(ruta):
    """Lee rockola.toml (si existe) y pisa los valores por defecto."""
    global PORT, MAX_PER_PERSON, MAX_DURATION, VOLUME, NORMALIZAR, MPV_ARGS, PIN, IP_FIJA, MODO
    global GUARDAR_GB, CARPETA, PREDESCARGAR, AZAR_AL_PRENDER
    if not ruta.exists():
        return
    with open(ruta, "rb") as f:
        c = tomllib.load(f)
    PORT = int(c.get("puerto", PORT))
    MAX_PER_PERSON = int(c.get("max_por_persona", MAX_PER_PERSON))
    MAX_DURATION = int(c.get("duracion_maxima", MAX_DURATION))
    VOLUME = int(c.get("volumen", VOLUME))
    NORMALIZAR = bool(c.get("normalizar", NORMALIZAR))
    MPV_ARGS = [str(a) for a in c.get("mpv_args", MPV_ARGS)]
    PIN = str(c.get("pin", PIN)).strip()
    IP_FIJA = str(c.get("ip", IP_FIJA)).strip()
    MODO = str(c.get("modo", MODO)).strip()
    GUARDAR_GB = max(0.0, float(c.get("guardar_gb", GUARDAR_GB)))
    CARPETA = str(c.get("carpeta", CARPETA)).strip()
    PREDESCARGAR = max(0, int(c.get("predescargar", PREDESCARGAR)))
    AZAR_AL_PRENDER = bool(c.get("azar_al_prender", AZAR_AL_PRENDER))


def carpeta():
    """Donde quedan las canciones guardadas y los ajustes que se cambian desde la web."""
    if CARPETA:
        return Path(CARPETA).expanduser()
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "rockola"


def leer_ajustes():
    try:
        return json.loads((carpeta() / "ajustes.json").read_text())
    except (OSError, ValueError):
        return {}


def guardar_ajustes(**cambios):
    ruta = carpeta() / "ajustes.json"
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(leer_ajustes() | cambios))
        os.replace(tmp, ruta)
    except OSError as e:
        print(f"  ⚠ no pude guardar {ruta}: {e}", flush=True)


def bump(sala=None):
    """Marca un cambio en `sala` (sin sala: en todas). Llamar con el lock tomado."""
    state["version"] += 1
    for s in [sala] if sala else salas.values():
        s.version += 1
    changed.notify_all()


def avisar(texto, sala=None):
    sala = sala or salas[PRINCIPAL]
    with lock:
        n = (sala.notice or {}).get("id", 0) + 1
        sala.notice = {"id": n, "text": texto}
        bump(sala)
    print(f"  ⚠ {texto}" + ("" if sala.principal else f"  (sala {sala.nombre})"), flush=True)


# --------------------------------------------------------------------- salas

def sala_de(sid):
    """La sala con ese id ("" o None: la principal). Si no existe (la borraron), ApiError."""
    s = salas.get(str(sid or PRINCIPAL))
    if s is None:
        raise ApiError(404, "Esa sala ya no existe")
    return s


def _hash_pin(pin, sal):
    return hashlib.sha256(f"{sal}:{pin}".encode()).hexdigest()


def _slug(nombre):
    """«Ventas Norte» -> "ventas-norte": el id de la sala, para el link (?sala=…)."""
    t = unicodedata.normalize("NFKD", nombre.casefold())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:24].strip("-") or "sala"


def leer_salas():
    """Las salas creadas desde la web (salas.json). Su cola y su chat no se guardan."""
    try:
        datos = json.loads((carpeta() / "salas.json").read_text())
    except (OSError, ValueError):
        return
    for d in datos if isinstance(datos, list) else []:
        try:
            s = Sala(str(d["id"]), str(d["nombre"])[:30], pin=str(d["pin"]), sal=str(d["sal"]))
        except (KeyError, TypeError):
            continue
        if s.id != PRINCIPAL and s.id not in salas and len(salas) < MAX_SALAS:
            s.shuffle = bool(d.get("aleatorio"))
            salas[s.id] = s


def guardar_salas():
    """Con el lock tomado: escribe salas.json."""
    ruta = carpeta() / "salas.json"
    datos = [{"id": s.id, "nombre": s.nombre, "pin": s.pin, "sal": s.sal, "aleatorio": s.shuffle}
             for s in salas.values() if not s.principal]
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False))
        os.replace(tmp, ruta)
    except OSError as e:
        print(f"  ⚠ no pude guardar {ruta}: {e}", flush=True)


def _nombre_y_pin(body):
    """El nombre y el PIN que eligieron para una sala nueva (de música o de tv)."""
    nombre = " ".join(str(body.get("name", "")).split())[:30]
    pin = str(body.get("pin", "")).strip()
    if not nombre:
        raise ApiError(400, "Ponele un nombre a la sala")
    if len(pin) < 4 or len(pin) > 30:
        raise ApiError(400, "El PIN de la sala tiene que tener entre 4 y 30 caracteres")
    return nombre, pin


def crear_sala(body):
    """Una sala nueva, sin parlante (se escucha con 📻 radio). Cualquiera la puede crear; para
    controlarla (pausa, ⏭, borrarla) hace falta su PIN, o el de la rockola."""
    if not director:
        raise ApiError(409, "Las salas andan sólo en modo mpv")
    nombre, pin = _nombre_y_pin(body)
    with lock:
        if any(s.nombre.casefold() == nombre.casefold() for s in salas.values()):
            raise ApiError(409, f"Ya hay una sala «{nombre}»")
        if len(salas) >= MAX_SALAS:
            raise ApiError(429, f"Ya hay {MAX_SALAS} salas: borren alguna que no se use")
        base = sid = _slug(nombre)
        n = 1
        while sid in salas or sid == PRINCIPAL:
            n += 1
            sid = f"{base}-{n}"
        sal = uuid.uuid4().hex
        s = salas[sid] = Sala(sid, nombre, pin=_hash_pin(pin, sal), sal=sal)
        guardar_salas()
        bump()   # todas: que aparezca en la lista
    teles.avisar()   # (y en la de las páginas de las salas de tv)
    print(f"  🚪 Sala nueva: {nombre}", flush=True)
    return s.publica()


def borrar_sala(sala):
    if sala.principal:
        raise ApiError(400, "La sala principal no se puede borrar")
    with lock:
        if salas.get(sala.id) is not sala:
            return
        del salas[sala.id]
        guardar_salas()
        for g in state["saving"]:   # lo que esperaba bajarse sólo para esta sala, ya no
            if sala.id in g["salas"]:
                g["salas"].remove(sala.id)
        state["saving"] = [g for g in state["saving"] if g["salas"] or g["error"]]
        bump()   # todas: que desaparezca de la lista (y las páginas que estaban en ella se enteren)
    teles.avisar()
    print(f"  🚪 Sala borrada: {sala.nombre}", flush=True)
    if stats:
        stats.borrar_sala(sala.id)
    if listas:
        listas.borrar_sala(sala.id)
    if director:
        threading.Thread(target=director.descargas.quitar_sala, args=(sala.id,), daemon=True).start()


def en_uso():
    """Las canciones que no se pueden borrar: en cada sala, la que suena, las que vienen (las que
    se bajan por adelantado) y la anterior (para que ⏮ sea instantáneo)."""
    with lock:
        usa = set()
        for s in salas.values():
            usa |= {i["videoId"] for i in [s.now, *s.queue[:PREDESCARGAR or None], *s.history[-1:]] if i}
        return usa


chat_envios = {}   # client -> [cuándo mandó cada uno de sus últimos mensajes]


def chatear(sala, body):
    client = str(body.get("client", ""))[:64]
    name = str(body.get("by", "")).strip()[:30]
    texto = " ".join(str(body.get("text", "")).split())[:300]
    if not client:
        raise ApiError(400, "Falta el identificador; recargá la página")
    if not name:
        raise ApiError(400, "Poné tu nombre arriba primero")
    if not texto:
        raise ApiError(400, "Escribí algo")
    ahora = time.monotonic()
    with lock:
        recientes = [t for t in chat_envios.get(client, []) if ahora - t < 10]
        if len(recientes) >= 5:
            raise ApiError(429, "Más despacio: esperá unos segundos")
        if len(chat_envios) > 1000:
            chat_envios.clear()
        chat_envios[client] = recientes + [ahora]
        n = sala.chat[-1]["id"] + 1 if sala.chat else 1
        sala.chat = (sala.chat + [{"id": n, "by": name, "text": texto, "at": time.time(), "client": client}])[-MAX_CHAT:]
        bump(sala)


def escuchar(sala, client):
    """Con el lock tomado: `client` tiene la 📻 radio prendida en `sala` (si es nuevo, se avisa:
    una sala sin parlante que estaba en espera arranca a sonar)."""
    if not client:
        return
    nuevo = client not in sala.oyentes
    if nuevo and len(sala.oyentes) >= 200:
        return
    sala.oyentes[client] = time.monotonic()
    if nuevo:
        bump(sala)


def set_radio(sala, body):
    """📻 prendida o apagada en una página (para saber si alguien escucha la sala)."""
    client = str(body.get("client", ""))[:64]
    if not client:
        raise ApiError(400, "Falta el identificador; recargá la página")
    with lock:
        if body.get("on"):
            escuchar(sala, client)
        elif sala.oyentes.pop(client, None) is not None:
            bump(sala)


# ------------------------------------------------------- la cola, entre reinicios
# Lo que suena (y en qué segundo), la cola, quién pidió cada una y lo que sonó recién, de cada
# sala, quedan en colas.json: al volver a arrancar (p. ej. para aplicar un cambio) sigue donde
# estaba. Se escribe un par de segundos después de cada cambio, cada 30 s mientras suena algo
# (por si se corta la luz) y al apagar la rockola. El chat no: se borra.

CLAVES_ITEM = ("id", "videoId", "title", "artist", "duration", "thumb", "album", "by", "playlist", "azar")
MAX_COLA_GUARDADA = 2000
lock_colas = threading.Lock()   # (escriben el hilo de bucle_colas y main, al apagar)
colas_escritas = {"texto": None, "cerrado": False}


def foto_de_colas():
    """Con el lock tomado: lo que hay que guardar de cada sala para retomarla."""
    fotos = {}
    for s in salas.values():
        en_cola = {i["id"] for i in s.queue}
        fotos[s.id] = {
            "now": s.now,
            "t": round(progreso_ahora(s)["t"], 1) if s.now else 0,
            # la principal la pausa mpv; las otras, su reloj (si nadie las escucha, no es pausa)
            "pausa": bool(director and director.pausado) if s.principal else s.pausada,
            "queue": s.queue,
            "history": s.history,
            "owners": {k: v for k, v in s.owners.items() if k in en_cola},
        }
    return {"salas": fotos}


def guardar_colas(final=False):
    """Escribe colas.json, si cambió algo desde la última vez. `final`: al apagar (después de esa,
    ninguna más: lo que pase mientras se cierra no es como estaba)."""
    with lock_colas:
        if colas_escritas["cerrado"]:
            return
        colas_escritas["cerrado"] = final
        with lock:
            texto = json.dumps(foto_de_colas(), ensure_ascii=False)
        if texto == colas_escritas["texto"]:
            return
        ruta = carpeta() / "colas.json"
        try:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            tmp = ruta.with_suffix(".tmp")
            tmp.write_text(texto)
            os.replace(tmp, ruta)
            colas_escritas["texto"] = texto
        except OSError as e:
            print(f"  ⚠ no pude guardar {ruta}: {e}", flush=True)


def bucle_colas(cada=30, junta=2):
    """Guarda las colas un ratito después de cada cambio (así se juntan los que vienen seguidos,
    como al cargar una playlist) y, mientras suena algo, cada `cada` segundos."""
    visto = None
    while True:
        with changed:
            changed.wait_for(lambda: state["version"] != visto, timeout=cada)
            visto = state["version"]
        time.sleep(junta)
        try:
            guardar_colas()
        except Exception as e:   # no debería pasar: que no se muera el hilo
            print(f"  ⚠ no pude guardar las colas: {e!r}", flush=True)


def _item_guardado(i):
    """Una canción leída de colas.json, sólo con lo que se usa, o None si no está bien."""
    if not isinstance(i, dict) or not isinstance(i.get("id"), str) or not VIDEO_ID.match(str(i.get("videoId", ""))):
        return None
    try:
        duracion = int(i.get("duration") or 0)
    except (TypeError, ValueError):
        return None
    if duracion <= 0:
        return None
    item = {k: i[k] for k in CLAVES_ITEM if k in i}
    item["duration"] = duracion
    if not isinstance(item.get("playlist", {}), dict):
        del item["playlist"]
    return item


def retomar_colas(en_pausa=False):
    """Al arrancar: cada sala como estaba la última vez (lo que sonaba y en qué segundo, la cola,
    lo que sonó recién). Con `en_pausa`, lo de la principal queda en pausa (ver main: a las 6 de la
    mañana la oficina está vacía). Devuelve True si la principal tiene una que seguía sonando (que
    no estaba en pausa)."""
    ruta = carpeta() / "colas.json"
    try:
        guardadas = json.loads(ruta.read_text())["salas"]
        if not isinstance(guardadas, dict):
            raise TypeError("«salas» no es un diccionario")
    except FileNotFoundError:
        return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"  ⚠ no pude leer {ruta} ({e!r}): arranco con las colas vacías", flush=True)
        return False
    seguia, cuantas = False, 0
    with lock:
        for sid, d in guardadas.items():
            s = salas.get(sid)
            if s is None or not isinstance(d, dict):
                continue   # una sala que ya no existe
            canciones = lambda k: [i for i in map(_item_guardado, d.get(k) or []) if i] if isinstance(d.get(k), list) else []
            s.queue = canciones("queue")[:MAX_COLA_GUARDADA]
            s.history = canciones("history")[-30:]
            en_cola = {i["id"] for i in s.queue}
            duenos = d.get("owners") if isinstance(d.get("owners"), dict) else {}
            s.owners = {k: str(v)[:64] for k, v in duenos.items() if k in en_cola}
            now = _item_guardado(d.get("now"))
            if now:
                try:
                    t = max(0.0, min(float(d.get("t") or 0), now["duration"]))
                except (TypeError, ValueError):
                    t = 0.0
                pausa = bool(d.get("pausa"))
                s.now = now
                s.progress = {"t": t, "d": now["duration"], "playing": False}
                if s.principal:
                    s.reanudar = {"id": now["id"], "t": t, "pausada": pausa or en_pausa}
                    seguia = not pausa
                else:
                    s.reanudar = {"id": now["id"], "t": t, "pausada": pausa}
                    s.pausada = pausa
            cuantas += len(s.queue) + bool(s.now)
            for i in [s.now, *s.queue, *s.history]:   # (para sumarlas a una ♫ playlist sin preguntarle a YouTube)
                if i and i.get("title"):
                    _remember({k: i.get(k) or ("" if k != "duration" else 0) for k in DATOS_TEMA})
            bump(s)
    if cuantas:
        print(f"  ⏯ Retomo donde estaba: {cuantas} {'canción' if cuantas == 1 else 'canciones'} entre lo que "
              "sonaba y las colas", flush=True)
    return seguia


# ------------------------------------------------------------- YouTube Music

def yt():
    # Una instancia por hilo: requests.Session no es thread-safe.
    if not hasattr(_local, "yt"):
        _local.yt = YTMusic()
    return _local.yt


def _thumb(thumbs):
    return thumbs[-1]["url"] if thumbs else ""


def _album(r):
    a = r.get("album")
    return str((a.get("name") if isinstance(a, dict) else a) or "").strip()


def album_de(video_id):
    """El álbum de una canción según YouTube Music ("" si es un video suelto).

    Si YouTube no responde, tira la excepción: no es que no tenga álbum.
    """
    try:
        w = yt().get_watch_playlist(videoId=video_id, limit=1)
        t = (w.get("tracks") or [{}])[0]
    except (KeyError, IndexError, TypeError, ValueError):
        return ""   # ytmusicapi no entendió la respuesta: no insistir
    return _album(t) if t.get("videoId") == video_id else ""


letras = {}            # videoId -> su letra (también las que no tienen: no volver a preguntar)
letras_en_camino = {}  # videoId -> Event: si la piden varios a la vez, se busca una sola vez
lock_letras = threading.Lock()


def letra(video_id):
    """La letra de una canción según YouTube Music, con el tiempo de cada renglón si lo tiene:

        {"synced": True, "source": "LyricFind", "lines": [{"t": 9.2, "f": 10.63, "text": "…"}, …]}

    (`t` y `f`: cuándo empieza y termina, en segundos). Sin tiempos, `synced` es False y los
    renglones sólo traen `text`; sin letra, `lines` viene vacío. Si YouTube no responde, tira
    la excepción y no queda guardado: se vuelve a probar la próxima vez.
    """
    with lock_letras:
        if video_id in letras:
            return letras[video_id]
        espera = letras_en_camino.get(video_id)
        if espera is None:
            espera = letras_en_camino[video_id] = threading.Event()
            mia = True
        else:
            mia = False
    if not mia:   # la está buscando otro pedido: esperarlo
        espera.wait(30)
        with lock_letras:
            if video_id in letras:
                return letras[video_id]
        raise ApiError(502, "No pude traer la letra. Probá de nuevo.")
    try:
        datos, guardar = _buscar_letra(video_id)
        if guardar:
            with lock_letras:
                if len(letras) > 500:
                    letras.clear()
                letras[video_id] = datos
        return datos
    finally:
        with lock_letras:
            letras_en_camino.pop(video_id, None)
        espera.set()


def _buscar_letra(video_id):
    """(letra, si se puede guardar). Primero la de YouTube Music (que la licencia a LyricFind o a
    Musixmatch); si no tiene, o la tiene sin tiempos, la de LRCLIB. Si las dos están sin tiempos,
    queda la de YouTube Music."""
    de_yt, guardar = _letra_youtube(video_id)
    if de_yt["synced"]:
        return de_yt, guardar
    try:
        meta = lookup(video_id)
        otra = _letra_lrclib(meta) if meta else None
    except Exception as e:   # sin internet, LRCLIB no contesta…: probar la próxima vez
        print(f"  ⚠ LRCLIB no respondió: {e}", flush=True)
        return de_yt, False
    if otra and (otra["synced"] or not de_yt["lines"]):
        return otra, guardar or otra["synced"]
    return de_yt, guardar


def _letra_youtube(video_id):
    """(letra, si se puede guardar), según YouTube Music."""
    from ytmusicapi.exceptions import YTMusicServerError

    sin_letra = {"synced": False, "source": "", "lines": []}
    try:
        browse = (yt().get_watch_playlist(videoId=video_id, limit=1) or {}).get("lyrics")
        if not browse:
            return sin_letra, True
        l = yt().get_lyrics(browse, timestamps=True)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return sin_letra, True    # ytmusicapi no entendió la respuesta: como si no tuviera
    except YTMusicServerError:
        return sin_letra, False   # p. ej. un video que YouTube Music no conoce (o falló un rato)
    if not l:
        return sin_letra, True
    fuente = str(l.get("source") or "").removeprefix("Source:").strip()
    if l.get("hasTimestamps"):
        return {"synced": True, "source": fuente,
                "lines": [{"t": r.start_time / 1000, "f": r.end_time / 1000, "text": r.text}
                          for r in l["lyrics"]]}, True
    texto = str(l.get("lyrics") or "")
    return {"synced": False, "source": fuente, "lines": [{"text": r} for r in texto.splitlines()]}, True


LRCLIB = "https://lrclib.net"
LRC_TIEMPO = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")   # [01:23.45]
LRC_PALABRA = re.compile(r"<\d+:\d+(?:[.:]\d+)?>")         # <01:23.45>, del LRC "mejorado"
TOLERANCIA_LRCLIB = 3   # segundos de diferencia con la duración del tema para creerle los tiempos


def _lrclib(path, **q):
    """Un pedido a la API de LRCLIB. None si no lo tiene (404); si no responde, tira la excepción.
    (Pide que cada programa diga quién es en el User-Agent.)"""
    pedido = urllib.request.Request(f"{LRCLIB}{path}?{urlencode(q)}",
                                    headers={"User-Agent": "Rockola/2.0 (rockola de oficina)"})
    try:
        with urllib.request.urlopen(pedido, timeout=8) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _leer_lrc(texto):
    """Un LRC ("[00:23.62] Ella durmió…") -> [{"t", "f", "text"}], como la letra de YouTube Music
    (`f`: cuándo empieza el renglón siguiente; el último no tiene)."""
    offset, renglones = 0.0, []
    for linea in str(texto or "").splitlines():
        resto = linea.strip()
        m = re.fullmatch(r"\[offset:\s*([+-]?\d+)\]", resto, re.I)
        if m:   # (en ms; positivo = la letra va antes)
            offset = int(m[1]) / 1000
            continue
        tiempos = []
        while m := LRC_TIEMPO.match(resto):   # (un renglón que se repite puede tener varios)
            tiempos.append(int(m[1]) * 60 + float(m[2].replace(":", ".")))
            resto = resto[m.end():]
        if tiempos:   # (los [ar:…], [ti:…] no tienen tiempo: no son letra)
            texto_r = " ".join(LRC_PALABRA.sub("", resto).split())
            renglones += [{"t": t, "text": texto_r} for t in tiempos]
    renglones.sort(key=lambda r: r["t"])
    for r in renglones:
        r["t"] = round(max(0.0, r["t"] - offset), 3)
    for r, sig in zip(renglones, renglones[1:]):
        r["f"] = sig["t"]
    return renglones


def _titulo_limpio(titulo, artista):
    """Para buscar en LRCLIB un tema pedido por link, que suele venir con el nombre del video:
    sin «(Official Video)», «[Remastered 2009]», «- Remasterizado» ni el artista adelante."""
    t = re.sub(r"\s*[(\[][^)\]]*[)\]]", "", titulo)
    t = re.sub(r"\s+[-–—]\s+[^-–—]*(remaster|versi[oó]n|version|edit|mix|mono)[^-–—]*$", "", t, flags=re.I)
    if artista:
        t = re.sub(rf"^\s*{re.escape(artista)}\s*[-–—]\s*", "", t, flags=re.I)
    return t.strip()


def _letra_lrclib(meta):
    """La letra según LRCLIB (lrclib.net: letras con tiempos que sube la comunidad; API libre, sin
    cuenta). Como la de YouTube Music, o None si no la tiene. Si no responde, tira la excepción.

    Primero pide el tema exacto (título, artista, álbum y duración); si no está, lo busca por
    título y artista. Los tiempos sólo se usan si la duración es la del tema (±3 s): si no, es otra
    versión (en vivo, con intro…) y los renglones irían corridos; ahí queda la letra sin tiempos.
    """
    from reproductor import _para_buscar, _primer_artista

    titulo = str(meta.get("title") or "").strip()
    artista = _primer_artista(meta.get("artist"))
    album = str(meta.get("album") or "").strip()
    dur = int(meta.get("duration") or 0)
    if not titulo or not artista:
        return None
    candidatos = []
    if album and dur:
        exacto = _lrclib("/api/get", track_name=titulo, artist_name=artista, album_name=album, duration=dur)
        candidatos = [exacto] if exacto else []
    if not candidatos:
        for t in dict.fromkeys(x for x in (titulo, _titulo_limpio(titulo, artista)) if x):
            candidatos = _lrclib("/api/search", track_name=t, artist_name=artista) or []
            if candidatos:
                break
    buscado = (_para_buscar(_titulo_limpio(titulo, artista)), _para_buscar(artista))
    cerca = lambda c: bool(dur) and abs(float(c.get("duration") or 0) - dur) <= TOLERANCIA_LRCLIB
    validos = [c for c in candidatos if isinstance(c, dict)
               and buscado[1] in _para_buscar(c.get("artistName"))   # (que no sea de otro artista)
               and (c.get("syncedLyrics") or c.get("plainLyrics") or c.get("instrumental"))]
    if not validos:
        return None
    mejor = max(validos, key=lambda c: (cerca(c), _para_buscar(_titulo_limpio(str(c.get("trackName") or ""), ""))
                                        == buscado[0], bool(c.get("syncedLyrics"))))
    if mejor.get("instrumental") and not (mejor.get("syncedLyrics") or mejor.get("plainLyrics")):
        return {"synced": False, "source": "LRCLIB", "lines": [], "instrumental": True}
    if cerca(mejor) and mejor.get("syncedLyrics"):
        renglones = _leer_lrc(mejor["syncedLyrics"])
        if renglones:
            return {"synced": True, "source": "LRCLIB", "lines": renglones}
    texto = mejor.get("plainLyrics") or "\n".join(r["text"] for r in _leer_lrc(mejor.get("syncedLyrics")))
    renglones = [{"text": r} for r in str(texto).splitlines()]
    return {"synced": False, "source": "LRCLIB", "lines": renglones} if any(r["text"].strip() for r in renglones) else None


def _remember(m):
    if len(meta_cache) > 5000:
        meta_cache.clear()
    meta_cache[m["videoId"]] = m
    return m


def lookup(video_id, fresh=False):
    if video_id in meta_cache and not fresh:
        return meta_cache[video_id]
    guardada = director.descargas.datos(video_id) if director and not fresh else None
    if guardada and guardada.get("duration"):   # pedida desde la lista de guardadas
        return _remember(guardada)
    d = (yt().get_song(video_id) or {}).get("videoDetails") or {}
    if d.get("videoId") != video_id:
        return None
    return _remember({
        "videoId": video_id,
        "title": d.get("title", ""),
        "artist": d.get("author", "").removesuffix(" - Topic"),
        "duration": int(d.get("lengthSeconds") or 0),
        "thumb": _thumb((d.get("thumbnail") or {}).get("thumbnails")),
    })


def search(q):
    m = URL_ID.search(q)
    if m:  # pegaron un link de YouTube / YouTube Music
        found = lookup(m.group(1))
        return [found] if found else []
    if PLAYLIST_ID.search(q):  # link de una playlist (sin tema): eso va por /api/playlist
        return []
    out = []
    for r in yt().search(q, filter="songs", limit=15):
        if not r.get("videoId"):
            continue
        out.append(_remember({
            "videoId": r["videoId"],
            "title": r.get("title") or "",
            "artist": ", ".join(a["name"] for a in r.get("artists") or []),
            "duration": r.get("duration_seconds") or 0,
            "thumb": _thumb(r.get("thumbnails")),
            "album": _album(r),
        }))
    return out


TIPOS_DE_DISCO = {"album": "Álbum", "ep": "EP"}


def buscar_albumes(q):
    """Los álbumes de YouTube Music que coinciden con lo buscado, para guardarlos enteros (⬇).

    Sólo los que tienen todas las palabras buscadas en el nombre o en el artista: buscando un
    disco («bocanada») o un artista («cerati») aparecen; buscando un tema («puente cerati»), no,
    que ahí sobran (YouTube igual devuelve los discos de Cerati).
    """
    from reproductor import _para_buscar

    if URL_ID.search(q) or PLAYLIST_ID.search(q):
        return []
    palabras = re.findall(r"\w+", _para_buscar(q))
    out = []
    for r in yt().search(q, filter="albums", limit=10):
        bid = r.get("browseId") or ""
        titulo = str(r.get("title") or "").strip()
        artista = ", ".join(a["name"] for a in r.get("artists") or [] if a.get("name"))
        if not ALBUM_ID.match(bid) or not titulo or str(r.get("type")).casefold() == "single":
            continue   # (un single es un tema suelto: ya aparece entre las canciones)
        if not all(p in _para_buscar(f"{titulo} {artista}") for p in palabras):
            continue
        out.append({
            "browseId": bid,
            "title": titulo,
            "artist": artista,
            "year": str(r.get("year") or ""),
            "type": TIPOS_DE_DISCO.get(str(r.get("type") or "").casefold(), "Álbum"),
            "thumb": _thumb(r.get("thumbnails")),
        })
        if len(out) == MAX_ALBUMES:
            break
    return out


def album_llamado(nombre, artista):
    """El browseId del álbum de YouTube Music con ese nombre y ese primer artista: el de unas
    guardadas, para completarlo. (Preguntarle a YouTube por un tema no sirve: en lugar del tema
    del disco suele devolver el video, que no tiene álbum.)"""
    from reproductor import _para_buscar, _primer_artista

    clave = lambda t, a: (_para_buscar(t).strip(), _para_buscar(_primer_artista(a)))
    buscado = clave(nombre, artista)
    if buscado[0]:
        for r in yt().search(f"{nombre} {_primer_artista(artista)}", filter="albums", limit=10):
            art = ", ".join(a["name"] for a in r.get("artists") or [] if a.get("name"))
            if ALBUM_ID.match(r.get("browseId") or "") and clave(str(r.get("title") or ""), art) == buscado:
                return r["browseId"]
    raise ApiError(404, f"No encontré «{nombre}» en YouTube Music")


def _segundos(texto):
    """'4:41' o '1:02:03' -> segundos (0 si no se entiende)."""
    s = 0
    for parte in str(texto or "").split(":"):
        if not parte.strip().isdigit():
            return 0
        s = s * 60 + int(parte)
    return s


def get_playlist(pid):
    """Los temas de una playlist de YouTube o YouTube Music (también álbumes y mixes)."""
    hit = playlists.get(pid)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    try:
        if pid.startswith("RD"):
            # Los mixes ("radio") se arman al vuelo y get_playlist no los entiende.
            w = yt().get_watch_playlist(playlistId=pid, limit=50)
            titulo, autor, tapa = "Mix de YouTube", "", ""
            temas = [dict(t, duration_seconds=_segundos(t.get("length")), thumbnails=t.get("thumbnail"))
                     for t in w.get("tracks") or []]
            total = len(temas)
        else:
            p = yt().get_playlist(pid, limit=MAX_PLAYLIST)
            titulo = p.get("title") or "Playlist"
            autor = p.get("author") or ""
            autor = autor.get("name", "") if isinstance(autor, dict) else str(autor)
            tapa = _thumb(p.get("thumbnails"))
            temas = p.get("tracks") or []
            total = int(p.get("trackCount") or len(temas))
    except (KeyError, IndexError, TypeError, ValueError):
        # ytmusicapi no encuentra lo que espera en la respuesta de YouTube
        raise ApiError(404, "No pude abrir esa playlist. ¿Es privada, o el link está incompleto?")
    canciones, no_disponibles = _canciones(temas, tapa)
    datos = {"id": pid, "title": titulo.strip()[:120], "author": autor.strip()[:60],
             "thumb": tapa or (canciones[0]["thumb"] if canciones else ""),
             "songs": canciones[:MAX_PLAYLIST], "total": total, "unavailable": no_disponibles}
    if len(playlists) > 50:
        playlists.clear()
    playlists[pid] = (time.monotonic(), datos)
    return datos


def _canciones(temas, tapa=""):
    """Los temas de una playlist o de un álbum, como los de search(), sin repetidos.
    Devuelve (canciones, cuántas no están disponibles)."""
    canciones, vistos, no_disponibles = [], set(), 0
    for t in temas:
        vid = t.get("videoId") or ""
        if not VIDEO_ID.match(vid) or t.get("isAvailable") is False:
            no_disponibles += 1
            continue
        if vid in vistos:
            continue
        vistos.add(vid)
        canciones.append(_remember({
            "videoId": vid,
            "title": t.get("title") or "",
            "artist": ", ".join(a["name"] for a in t.get("artists") or [] if a.get("name")),
            "duration": int(t.get("duration_seconds") or 0),
            "thumb": _thumb(t.get("thumbnails")) or tapa,
            "album": _album(t),
        }))
    return canciones, no_disponibles


def get_album(bid):
    """Los temas de un álbum de YouTube Music (su browseId, «MPRE…», viene de buscar_albumes)."""
    hit = albumes.get(bid)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    try:
        a = yt().get_album(bid)
        titulo = str(a.get("title") or "").strip()
        tapa = _thumb(a.get("thumbnails"))
        artista = ", ".join(x["name"] for x in a.get("artists") or [] if x.get("name"))
        # get_album ya les pone a los temas el nombre del álbum (y su artista, si no tienen).
        canciones, no_disponibles = _canciones(a.get("tracks") or [], tapa)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise ApiError(404, "No pude abrir ese álbum")
    datos = {"browseId": bid, "title": titulo[:120] or "Álbum", "artist": artista[:60], "thumb": tapa,
             "songs": canciones, "unavailable": no_disponibles}
    if len(albumes) > 50:
        albumes.clear()
    albumes[bid] = (time.monotonic(), datos)
    return datos


def _id_playlist(q):
    m = PLAYLIST_ID.search(str(q or ""))
    if not m:
        raise ApiError(400, "Eso no es un link de una playlist")
    return m.group(1)


def _se_puede(song):
    return bool(song["duration"]) and not (MAX_DURATION and song["duration"] > MAX_DURATION)


def playlist_info(q):
    """Lo que muestra la página antes de cargarla: nombre, cuántos temas, cuánto dura."""
    pl = get_playlist(_id_playlist(q))
    ok = [c for c in pl["songs"] if _se_puede(c)]
    return {"title": pl["title"], "author": pl["author"], "thumb": pl["thumb"],
            "count": len(ok), "duration": sum(c["duration"] for c in ok), "total": pl["total"]}


def _contar(n, uno, varios):
    return f"{n} {uno if n == 1 else varios}"


def _no_se_puede(vivo, largas):
    """Por qué quedaron afuera las que no se pueden pedir, como en `coleccion`."""
    minutos = MAX_DURATION // 60
    return [(largas, f"dura más de {minutos} minutos", f"duran más de {minutos} minutos"),
            (vivo, "no dice cuánto dura (¿en vivo?)", "no dicen cuánto duran (¿en vivo?)")]


def _afuera(motivos):
    quedaron = [_contar(n, uno, varios) for n, uno, varios in motivos if n > 0]
    return f" Quedaron afuera: {', '.join(quedaron)}." if quedaron else ""


def _generos_de(sid):
    """El género de cada artista de las guardadas de una sala (y cuántos falta averiguar): ver
    Estadisticas.generos_de."""
    if not director:
        raise ApiError(409, "Las canciones guardadas sólo existen en modo mpv")
    if not stats:
        raise ApiError(409, "Los géneros se anotan con las estadísticas, que no andan: mirá los avisos en la consola de la rockola")
    try:
        return stats.generos_de(director.descargas.artistas(sid))
    except sqlite3.Error as e:
        print(f"  ⚠ no pude leer los géneros: {e}", flush=True)
        raise ApiError(500, "No pude leer los géneros")


def averiguar_generos():
    """Que se vaya averiguando el género de los artistas de todas las guardadas, también los de las
    que nunca sonaron (p. ej. las que se bajaron con ⬇), para cuando las miren por género."""
    try:
        _generos_de(None)
    except ApiError:
        pass


def genero_guardado(sid, clave):
    """Las guardadas de la sala que son de un género (`clave`: su "key" en la lista de géneros)."""
    de, _ = _generos_de(sid)
    g = director.descargas.del_genero(de, clave, sala=sid)
    if not g:
        raise ApiError(404, "No hay guardadas de ese género")
    return g


def coleccion(sala, body):
    """Una colección de temas entera, para cargarla a la cola o sumarla a una playlist, según
    `from`: una playlist de la rockola (`list`, su id), un álbum de YouTube Music (`browseId`), uno de las
    guardadas (`album`, `artist` y sus `videoIds`), una playlist de YouTube (`q`, el link) o las
    guardadas de un género (`genre`, su clave).
    Devuelve {title, kind, songs, afuera}: `kind` es "album", "lista", "youtube" o "genero"; `afuera`,
    los que no vinieron, como (cuántos, uno, varios)."""
    de = body.get("from")
    if de == "genre":
        # No tiene un orden: van mezcladas (y, si son muchas, las que entran: cada vez, otras).
        g = genero_guardado(sala.id, str(body.get("genre", ""))[:80])
        temas = g["songs"]
        random.shuffle(temas)
        maximo = f"del máximo de {MAX_PLAYLIST} por vez"
        return {"title": g["genre"] or "Sin género", "kind": "genero", "songs": temas[:MAX_PLAYLIST],
                "afuera": [(len(temas) - MAX_PLAYLIST, f"pasa {maximo}", f"pasan {maximo}")]}
    if de == "list":
        titulo, temas = listas.temas(sala.id, str(body.get("list", "")))
        return {"title": titulo, "kind": "lista", "songs": temas, "afuera": []}
    if de == "album":
        bid = str(body.get("browseId", ""))
        if not ALBUM_ID.match(bid):
            raise ApiError(400, "Álbum inválido")
        al = get_album(bid)
        return {"title": al["title"], "kind": "album", "songs": al["songs"],
                "afuera": [(al["unavailable"], "no está disponible", "no están disponibles")]}
    if de == "saved":
        # Uno de las guardadas: entero y en el orden del disco, como está en YouTube Music (las
        # guardadas están por orden alfabético, y capaz faltan temas). Si no lo encuentra, las guardadas.
        nombre, artista = str(body.get("album") or "").strip()[:200], str(body.get("artist") or "")[:200]
        if not nombre:
            raise ApiError(400, "Álbum inválido")
        try:
            al = get_album(album_llamado(nombre, artista))
            return {"title": al["title"], "kind": "album", "songs": al["songs"],
                    "afuera": [(al["unavailable"], "no está disponible", "no están disponibles")]}
        except Exception as e:   # (no está en YouTube Music, o YouTube no contesta)
            print(f"  ⚠ no encontré «{nombre}» en YouTube Music ({e}): uso las guardadas", flush=True)
        vids = [str(v) for v in (body.get("videoIds") or [])[:MAX_PLAYLIST] if VIDEO_ID.match(str(v))]
        temas = [d for d in (director.descargas.datos(v) for v in vids) if d] if director else []
        if not temas:
            raise ApiError(404, f"No encontré los temas de «{nombre}»")
        return {"title": nombre, "kind": "album", "songs": temas, "afuera": []}
    if de == "youtube":
        pl = get_playlist(_id_playlist(body.get("q")))
        maximo = f"del máximo de {MAX_PLAYLIST} por playlist"
        return {"title": pl["title"], "kind": "youtube", "songs": pl["songs"],
                "afuera": [(pl["unavailable"], "no está disponible", "no están disponibles"),
                           (pl["total"] - MAX_PLAYLIST, f"pasa {maximo}", f"pasan {maximo}")]}
    raise ApiError(400, "No sé qué cargar")


def cargar_entera(sala, body):
    """+ a la cola / ▶ reproducir (con el PIN): todos los temas de una colección (ver `coleccion`),
    en orden. Con `at` = "now" suena ya: corta la que está sonando (queda en "sonaron recién",
    como con ⏭), el primero suena y los demás quedan primeros en la cola, antes de lo que ya
    estaba (si alguno estaba en la cola, se mueve acá). Si no, van al final, sin los que ya
    están en la cola. Sin dueño: no cuentan para el límite por persona de quien la cargó.
    Devuelve un resumen para mostrar."""
    col = coleccion(sala, body)
    ahora = body.get("at") == "now"
    name = str(body.get("by", "")).strip()[:30] or {"album": "Álbum", "genero": "Género"}.get(col["kind"], "Playlist")
    # (para la cola: cada tema dice de dónde vino, y con el PIN se saca entera de un toque)
    lista = {"id": uuid.uuid4().hex[:10], "title": col["title"], "kind": col["kind"]}
    vivo = largas = repetidas = 0
    with lock:
        ya = set()
        if not ahora:
            ya = {i["videoId"] for i in sala.queue} | ({sala.now["videoId"]} if sala.now else set())
        nuevas, vistos = [], set()
        for c in col["songs"]:
            if not c["duration"]:
                vivo += 1
            elif not _se_puede(c):
                largas += 1
            elif c["videoId"] in ya or c["videoId"] in vistos:
                repetidas += 1
            else:
                vistos.add(c["videoId"])
                nuevas.append(dict({k: c.get(k) for k in DATOS_TEMA}, id=uuid.uuid4().hex[:10], by=name,
                                   playlist=lista))
        if nuevas and ahora:
            movidas = {i["id"] for i in sala.queue if i["videoId"] in vistos}
            for k in movidas:
                sala.owners.pop(k, None)
            sala.queue = nuevas[1:] + [i for i in sala.queue if i["id"] not in movidas]
            sala.pausada = False   # (sin parlante: si estaba pausada, que suene)
            _sonar(sala, nuevas[0])
        elif nuevas:
            sala.queue.extend(nuevas)
            bump(sala)
    afuera = _afuera([(repetidas, "ya estaba en la cola", "ya estaban en la cola"),
                      *_no_se_puede(vivo, largas), *col["afuera"]])
    if not nuevas:
        raise ApiError(409, f"No cargué ningún tema de «{col['title']}».{afuera}")
    temas = _contar(len(nuevas), "tema", "temas")
    if ahora:
        texto = f"Suena «{col['title']}» ({temas}); lo que estaba en la cola sigue después.{afuera}"
    else:
        texto = f"Cargué {temas} de «{col['title']}» al final de la cola.{afuera}"
    return {"added": len(nuevas), "text": texto}


def remove_playlist(sala, body):
    """Quita de la cola todos los temas que vinieron de una misma carga de playlist."""
    lista_id = str(body.get("playlist", ""))
    with lock:
        antes = len(sala.queue)
        sala.queue = [i for i in sala.queue if (i.get("playlist") or {}).get("id") != lista_id]
        if len(sala.queue) != antes:
            bump(sala)


# ------------------------------------------------- ♫ las playlists de la rockola
# Las arma cualquiera; las cambia quien la armó o alguien con el PIN (ver listas.py). Cargarlas
# enteras a la cola es con el PIN (cargar_entera); pedir sus temas de a uno, como cualquier tema.

def _cambio_en_listas(sala):
    with lock:
        bump(sala)   # (el estado lleva la versión de sus playlists: las páginas las piden de nuevo)


def para_la_lista(sala, body):
    """Los temas para sumar a una playlist: uno o varios sueltos (`videoIds`), o una colección
    entera (`from`, ver `coleccion`). Con las mismas reglas que para pedirlos. Devuelve
    (temas, título de la colección o None, los que quedaron afuera)."""
    if not body.get("from"):
        vids = body.get("videoIds")
        if not isinstance(vids, list) or not vids:
            raise ApiError(400, "¿Qué tema hay que sumar?")
        temas, malas = [], []
        for v in vids[:MAX_SUELTOS]:   # (los que no conoce, uno por uno a YouTube: la página manda de a uno)
            try:
                temas.append(cancion(str(v)))
            except ApiError as e:
                malas.append(e)
        if malas and not temas:
            raise malas[0]   # era una sola (o ninguna anduvo): por qué
        return temas, None, [(len(malas), "no se puede pedir", "no se pueden pedir")]
    col = coleccion(sala, body)
    vivo = sum(not c["duration"] for c in col["songs"])
    largas = sum(bool(c["duration"]) and not _se_puede(c) for c in col["songs"])
    return [c for c in col["songs"] if _se_puede(c)], col["title"], [*_no_se_puede(vivo, largas), *col["afuera"]]


def sumar_a_lista(sala, body, admin):
    client = str(body.get("client", ""))[:64]
    lid = str(body.get("id", ""))
    listas.puede(sala.id, lid, client, admin)   # antes de ir a buscar los temas a YouTube
    temas, de, afuera = para_la_lista(sala, body)
    n, repetidas, sin_lugar, lista = listas.sumar(sala.id, lid, temas, client, admin)
    _cambio_en_listas(sala)
    nombre = lista["name"]
    afuera = _afuera([(repetidas, "ya estaba", "ya estaban"),
                      (sin_lugar, f"no entró (máximo {MAX_TEMAS_LISTA})",
                       f"no entraron (máximo {MAX_TEMAS_LISTA})"), *afuera])
    if not n:
        if de is None and len(temas) == 1 and repetidas:
            raise ApiError(409, f"Ya está en «{nombre}»")
        raise ApiError(409, f"No sumé ningún tema a «{nombre}».{afuera}")
    if de is None and n == 1:
        texto = f"«{temas[0]['title']}» quedó en «{nombre}»"
    else:
        texto = f"Sumé {_contar(n, 'tema', 'temas')}" + (f" de «{de}»" if de else "") + f" a «{nombre}».{afuera}"
    return {"text": texto, "list": lista}


def editar_lista(sala, path, body, admin):
    """Los demás cambios en una playlist: crearla, cambiarle el nombre, borrarla, quitar o mover
    un tema. Devuelve lo que se le contesta a la página."""
    client = str(body.get("client", ""))[:64]
    lid = str(body.get("id", ""))
    if path == "/api/list-new":
        lista = listas.crear(sala.id, body.get("name"), body.get("by"), client)
        print(f"  ♫ Playlist nueva: {lista['name']}" + ("" if sala.principal else f" (sala {sala.nombre})"), flush=True)
        r = {"list": lista}
    elif path == "/api/list-rename":
        r = {"list": listas.renombrar(sala.id, lid, body.get("name"), client, admin)}
    elif path == "/api/list-delete":
        r = {"deleted": listas.borrar(sala.id, lid, client, admin)}
    elif path == "/api/list-remove":
        r = {"list": listas.quitar(sala.id, lid, str(body.get("videoId", "")), client, admin)}
    elif path == "/api/list-move":
        try:
            a = int(body.get("to", 0))
        except (TypeError, ValueError):
            raise ApiError(400, "Posición inválida")
        r = {"list": listas.mover(sala.id, lid, str(body.get("videoId", "")), a, client, admin)}
    else:
        raise ApiError(404, "No existe")
    _cambio_en_listas(sala)
    return r


# ------------------------------------------------------------------- la cola

def public_state(sala, client=None, since=None, radio=False):
    with changed:
        if radio:
            escuchar(sala, client)
        # Long-poll: si el cliente ya tiene esta versión, esperamos un cambio.
        if since is not None:
            hasta = time.monotonic() + 25
            while since == sala.version and salas.get(sala.id) is sala and time.monotonic() < hasta:
                changed.wait(timeout=hasta - time.monotonic())
        if salas.get(sala.id) is not sala:
            raise ApiError(404, "Esa sala ya no existe")
        ahora = time.monotonic()
        if radio and client in sala.oyentes:
            # Sigue escuchando (para que no se venza). Si mientras esperaba apagó la radio
            # (/api/radio), no: si no, la volvería a anotar.
            sala.oyentes[client] = ahora
        return {
            "version": sala.version,
            "mode": state["mode"],
            "room": sala.publica(),
            "rooms": [s.publica() for s in salas.values()],
            "tvs": teles.publicas(),   # las salas de tv (📺): otra página, /tv
            "now": sala.now,
            "queue": [dict(i, mine=bool(client) and sala.owners.get(i["id"]) == client)
                      for i in sala.queue],
            "history": sala.history[-10:][::-1],
            "progress": progreso_ahora(sala),
            # (sin parlante: hasta que la que suena esté bajada y arranque, "cargando")
            "loading": sala.loading or (not sala.principal and sala.now is not None and not sala.lista),
            "volume": state["volume"] if sala.principal else None,
            "notice": sala.notice,
            "shuffle": sala.shuffle,
            # Sin parlante: la pausaron (con el PIN), o está en espera porque nadie la escucha.
            "paused": None if sala.principal else sala.pausada,
            "waiting": not sala.principal and sala.now is not None and sala.lista and not sala.pausada
                       and not sala.progress["playing"],
            "listeners": sum(ahora - t < OYENTE_VENCE for t in sala.oyentes.values()),
            "chat": [{"id": m["id"], "by": m["by"], "text": m["text"], "at": m["at"],
                      "mine": bool(client) and m["client"] == client} for m in sala.chat[-50:]],
            "library": director.descargas.resumen(sala.id) if director else None,
            "lists": {"v": listas.version(sala.id)} if listas else None,   # (las pide aparte: /api/lists)
            "saving": [dict({k: g.get(k) for k in ("videoId", "title", "artist", "error", "album")},
                            active=g["bajando"], mine=bool(client) and client in g["clientes"])
                       for g in state["saving"] if sala.id in g["salas"]],
        }


def medir(p, t):
    """Anota por dónde va la canción y cuándo se midió (ver progreso_ahora). Con el lock tomado."""
    p["t"], p["at"] = t, time.monotonic()


def progreso_ahora(sala):
    """El progreso para mandarle a las páginas. `t` se mide de a un segundo (bucle_progreso):
    si está sonando, se lo adelanta lo que pasó desde que se midió. Si no, la página quedaría
    hasta un segundo atrasada (se nota en la letra). En las salas sin parlante es el reloj:
    sólo se mide al arrancar, al pausar y al seguir. Llamar con el lock tomado."""
    p = dict(sala.progress)
    at = p.pop("at", None)
    if at is not None and p["playing"]:
        p["t"] += time.monotonic() - at
        if p.get("d"):
            p["t"] = min(p["t"], p["d"])
    return p


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def cancion(vid):
    """Los datos de una canción que se puede pedir (o guardar). Si no se puede, ApiError."""
    if not VIDEO_ID.match(vid):
        raise ApiError(400, "Canción inválida")
    meta = lookup(vid)
    if meta and not meta["duration"]:
        meta = lookup(vid, fresh=True)  # la búsqueda a veces no trae la duración
    if not meta:
        raise ApiError(404, "No encontré esa canción")
    if not meta["duration"]:
        raise ApiError(400, "No se pueden pedir transmisiones en vivo")
    if MAX_DURATION and meta["duration"] > MAX_DURATION:
        raise ApiError(400, f"Es muy larga (máximo {MAX_DURATION // 60} minutos)")
    return meta


def add_song(sala, body):
    vid = str(body.get("videoId", ""))
    client = str(body.get("client", ""))[:64]
    name = str(body.get("by", "")).strip()[:30] or "Anónimo"
    if not VIDEO_ID.match(vid):
        raise ApiError(400, "Canción inválida")
    if not client:  # sin esto no hay límite por persona ni se sabe de quién es
        raise ApiError(400, "Falta el identificador; recargá la página")
    meta = cancion(vid)
    with lock:
        now = sala.now
        if (now and now["videoId"] == vid) or any(i["videoId"] == vid for i in sala.queue):
            raise ApiError(409, "Esa canción ya está en la cola")
        if sum(sala.owners.get(i["id"]) == client for i in sala.queue) >= MAX_PER_PERSON:
            raise ApiError(429, f"Ya hay {MAX_PER_PERSON} canciones tuyas en la cola. Esperá a que suene alguna.")
        item = dict(meta, id=uuid.uuid4().hex[:10], by=name)
        sala.queue.append(item)
        sala.owners[item["id"]] = client
        bump(sala)
    return item


def _se_guarda():
    if not director or not director.descargas.limite:
        raise ApiError(409, "Las canciones se guardan sólo en modo mpv y con guardar_gb mayor que 0")


def _a_guardar(meta, sala, client, album=None):
    """Lo que va en state["saving"]. `album`: el browseId, si la pidieron con el álbum entero.
    `salas`: las que la pidieron (al bajarse, queda entre las guardadas de cada una).
    `clientes`: quiénes (cada uno puede cancelar las suyas). `bajando`: es la que se está bajando."""
    return {"videoId": meta["videoId"], "title": meta["title"], "artist": meta["artist"],
            "datos": datos_de(meta), "error": None, "hasta": 0, "album": album, "salas": [sala.id],
            "clientes": [client] if client else [], "bajando": False}


def _sumar_pedido(g, sala, client):
    """Otra sala (u otra persona) pide guardar una que ya está esperando para bajarse."""
    if sala.id not in g["salas"]:
        g["salas"].append(sala.id)
    if client and client not in g["clientes"]:
        g["clientes"].append(client)


def save_song(sala, body):
    """Baja una canción para que quede entre las guardadas de la sala, sin ponerla en la cola
    (⬇ en la página). Las va bajando el Director, de a una (ver Director.bucle_guardar).

    Si ya estaba bajada (por otra sala), queda guardada también en esta, al toque: devuelve
    True. Si hay que bajarla, False."""
    _se_guarda()
    vid = str(body.get("videoId", ""))
    client = str(body.get("client", ""))[:64]
    if VIDEO_ID.match(vid) and director.descargas.datos(vid):
        if not director.descargas.sumar_a_sala(vid, sala.id):
            raise ApiError(409, "Esa canción ya está guardada")
        with lock:
            bump()   # (todas: cambió la cuenta de guardadas)
        return True
    meta = cancion(vid)   # las mismas reglas que para pedirla: nada en vivo ni muy largo
    with lock:
        # Si falló antes, se vuelve a intentar (y se va el aviso del error).
        state["saving"] = [g for g in state["saving"] if not (g["videoId"] == vid and g["error"])]
        ya = next((g for g in state["saving"] if g["videoId"] == vid), None)
        if ya and sala.id in ya["salas"]:
            raise ApiError(409, "Esa canción ya se está bajando")
        if ya:   # la está bajando otra sala: que quede también en esta
            _sumar_pedido(ya, sala, client)
        elif sum(not g["error"] for g in state["saving"]) >= MAX_GUARDANDO:
            raise ApiError(429, f"Ya hay {MAX_GUARDANDO} canciones esperando para bajarse. Probá en un rato.")
        else:
            state["saving"].append(_a_guardar(meta, sala, client))
        bump()
    return False


def save_album(sala, body):
    """⬇ de un álbum: baja los temas que faltan para que quede entero entre las guardadas de la
    sala, sin ponerlos en la cola. Con las mismas reglas que una canción (nada en vivo ni muy
    largo). Los que ya estaban bajados por otra sala quedan también en esta, sin bajarlos.
    Devuelve un resumen para mostrar."""
    _se_guarda()
    client = str(body.get("client", ""))[:64]
    if body.get("album"):   # desde las guardadas: se busca por nombre y artista
        bid = album_llamado(str(body["album"])[:200], str(body.get("artist", ""))[:200])
    else:
        bid = str(body.get("browseId", ""))
    if not ALBUM_ID.match(bid):
        raise ApiError(400, "Álbum inválido")
    al = get_album(bid)
    ids = {c["videoId"] for c in al["songs"]}
    bajadas = {v for v in ids if director.descargas.datos(v)}
    sumadas = {v for v in bajadas if director.descargas.sumar_a_sala(v, sala.id)}
    nuevas, bajando, largas, vivo, sin_lugar = [], 0, 0, 0, 0
    with lock:
        # Las que fallaron antes se vuelven a intentar (y se va el aviso del error).
        state["saving"] = [g for g in state["saving"] if not (g["error"] and g["videoId"] in ids)]
        en_fila = {g["videoId"]: g for g in state["saving"]}
        lugar = MAX_GUARDANDO - sum(not g["error"] for g in state["saving"])
        for c in al["songs"]:
            if c["videoId"] in bajadas:
                continue
            if c["videoId"] in en_fila:
                bajando += 1
                _sumar_pedido(en_fila[c["videoId"]], sala, client)   # (si la baja otra sala, también es de esta)
            elif not c["duration"]:
                vivo += 1
            elif not _se_puede(c):
                largas += 1
            elif len(nuevas) >= lugar:
                sin_lugar += 1
            else:
                nuevas.append(c)
        state["saving"].extend(_a_guardar(c, sala, client, album=bid) for c in nuevas)
        bump()   # aunque no haya nuevas: capaz se fueron avisos de error (o se sumaron guardadas)
    titulo, ya, minutos = al["title"], len(bajadas - sumadas), MAX_DURATION // 60
    if not al["songs"]:
        raise ApiError(409, f"«{titulo}» no tiene temas que se puedan bajar.")
    contar = lambda n, uno, varios: f"{n} {uno if n == 1 else varios}"
    hay = [contar(n, uno, varios) for n, uno, varios in (
        (ya, "ya estaba guardado", "ya estaban guardados"),
        (len(sumadas), "ya estaba bajado en otra sala (quedó también en esta)",
         "ya estaban bajados en otra sala (quedaron también en esta)"),
        (bajando, "ya se estaba bajando", "ya se estaban bajando"),
    ) if n > 0]
    quedaron = [contar(n, uno, varios) for n, uno, varios in (
        (largas, f"dura más de {minutos} minutos", f"duran más de {minutos} minutos"),
        (vivo, "no dice cuánto dura (¿en vivo?)", "no dicen cuánto duran (¿en vivo?)"),
        (al["unavailable"], "no está disponible", "no están disponibles"),
        (sin_lugar, "no entró en la fila de descargas", "no entraron en la fila de descargas"),
    ) if n > 0]
    afuera = f" Quedaron afuera: {', '.join(quedaron)}." if quedaron else ""
    if bajadas and not (nuevas or bajando or sin_lugar):
        # Ya está todo lo que se puede bajar. No es un error: la página lo marca como guardado.
        if sumadas:
            texto = (f"Quedó guardado «{titulo}» en esta sala: "
                     f"{contar(len(sumadas), 'tema ya estaba bajado', 'temas ya estaban bajados')} "
                     f"en otra.{afuera}")
        else:
            texto = (f"Ya están guardados todos los temas de «{titulo}»"
                     + (f" que se pueden bajar.{afuera}" if quedaron else "."))
        return {"browseId": bid, "added": 0, "complete": True, "text": texto}
    extra = (f" {', '.join(hay)}." if hay else "") + afuera + (
        " Tocá ⬇ de nuevo en un rato para bajar el resto." if sin_lugar else "")
    if not nuevas:
        raise ApiError(409, f"No hay temas para bajar de «{titulo}».{extra}")
    temas = "tema" if len(nuevas) == 1 else "temas"
    return {"browseId": bid, "added": len(nuevas), "complete": False,
            "text": f"Bajando {len(nuevas)} {temas} de «{titulo}»: van a quedar en las guardadas "
                    f"(no se ponen en la cola).{extra}"}


def cancelar_guardado(sala, body, es_admin):
    """✕ en la lista de las que esperan para bajarse: ya no se guardan en esta sala. Cada uno
    cancela las suyas; con el PIN, cualquiera. Si ninguna otra sala la pidió, sale de la lista (si
    se estaba bajando, la descarga termina pero no queda guardada). Las que fallaron, se van.
    Devuelve cuántas se cancelaron."""
    client = str(body.get("client", ""))[:64]
    vids = {str(v) for v in (body.get("videoIds") or [])[:MAX_GUARDANDO]}
    n = ajenas = 0
    with lock:
        for g in state["saving"]:
            if g["videoId"] not in vids or sala.id not in g["salas"]:
                continue
            if not (es_admin or (client and client in g["clientes"])):
                ajenas += 1
                continue
            g["salas"].remove(sala.id)
            n += 1
        state["saving"] = [g for g in state["saving"] if g["salas"]]
        if n:
            bump()
    if ajenas and not n:
        raise ApiError(403, "Sólo podés cancelar las que pediste vos (con el PIN, cualquiera)")
    return n


def quitar_guardada(sala, body):
    """🗑 a mano (con el PIN): la canción deja de estar en las guardadas de la sala. Si no la
    tiene ninguna otra sala, se borra el archivo (si está sonando o en una cola, cuando deje de
    estarlo). Devuelve el texto para mostrar."""
    _se_guarda()
    vid = str(body.get("videoId", ""))
    if not VIDEO_ID.match(vid):
        raise ApiError(400, "Canción inválida")
    titulo = (director.descargas.datos(vid) or {}).get("title") or "la canción"
    with lock:
        vivas = list(salas)
    r = director.descargas.borrar_de_sala(vid, sala.id, en_uso(), vivas)
    if r is None:
        raise ApiError(404, "Esa canción no está entre las guardadas de esta sala")
    with lock:
        bump()   # (todas: cambió la lista de guardadas)
        otras = [salas[s].nombre for s in sorted(r["salas"]) if s in salas]
    print(f"  🗑 Borrada a mano: “{titulo}”" + (f" (sólo de la sala {sala.nombre})" if otras else ""), flush=True)
    if otras:
        return f"Saqué «{titulo}» de las guardadas de esta sala (sigue en {', '.join(otras)})"
    if r["pendiente"]:
        return f"Borré «{titulo}» de las guardadas (el archivo se borra cuando deje de sonar o salga de la cola)"
    return f"Borré «{titulo}»"


def remove_song(sala, body, is_local):
    item_id = str(body.get("id", ""))
    client = str(body.get("client", ""))
    with lock:
        if not is_local and (not client or sala.owners.get(item_id) != client):
            raise ApiError(403, "Sólo podés quitar tus propias canciones")
        sala.queue = [i for i in sala.queue if i["id"] != item_id]
        sala.owners.pop(item_id, None)
        bump(sala)


def advance(sala, body):
    """Termina `finished` y pasa a la siguiente.

    Es idempotente: si `finished` no es la que está sonando, no hace nada
    (evita saltear dos canciones si llegan dos avisos seguidos).
    """
    finished = body.get("finished")
    with lock:
        now = sala.now
        if (now["id"] if now else None) != finished:
            return False
        sig = sala.queue.pop(0) if sala.queue else cancion_al_azar(sala)
        if now is None and sig is None:
            return False   # no sonaba nada y sigue sin sonar: no cambió nada
        _sonar(sala, sig)
        return True


def _sonar(sala, item):
    """Que suene `item` (o nada): la que sonaba pasa a "sonaron recién" (y a las estadísticas,
    con lo que llegó a sonar). Con el lock tomado."""
    if sala.now:
        if stats and salas.get(sala.id) is sala:   # (si la borraron, sus estadísticas ya se fueron)
            stats.anotar(sala.id, sala.now, progreso_ahora(sala)["t"])
        sala.history = (sala.history + [sala.now])[-30:]
    sala.now = item
    if item:
        sala.owners.pop(item["id"], None)
    sala.progress = {"t": 0, "d": 0, "playing": False}
    sala.trabajo, sala.lista = None, False   # (sin parlante: Director.mover la baja y la arranca)
    sala.reanudar = None
    bump(sala)


def play_now(sala, body):
    """▶ ahora (con el PIN): corta la que suena (queda en "sonaron recién", como con ⏭) y pone
    esta. Si estaba en la cola, sale de ahí; el resto de la cola sigue igual."""
    meta = cancion(str(body.get("videoId", "")))   # las mismas reglas que para pedirla
    name = str(body.get("by", "")).strip()[:30] or "Anónimo"
    with lock:
        if sala.now and sala.now["videoId"] == meta["videoId"]:
            raise ApiError(409, "Ya está sonando")
        for i in sala.queue:
            if i["videoId"] == meta["videoId"]:
                sala.owners.pop(i["id"], None)
        sala.queue = [i for i in sala.queue if i["videoId"] != meta["videoId"]]
        sala.pausada = False   # (sin parlante: si estaba pausada, que suene)
        _sonar(sala, dict(meta, id=uuid.uuid4().hex[:10], by=name))


def cancion_al_azar(sala):
    """Con el aleatorio prendido, una de las canciones guardadas de la sala. Llamar con el lock tomado."""
    if not (sala.shuffle and director):
        return None
    ocupadas = {i["videoId"] for i in sala.queue}
    if sala.now:
        ocupadas.add(sala.now["videoId"])   # la que acaba de terminar
    c = director.descargas.al_azar(ocupadas, sala.id)
    return c and dict(c, id=uuid.uuid4().hex[:10], by="al azar", azar=True)


def arrancar_al_azar(sala):
    """Si no suena nada ni hay cola, que arranque una al azar (si el aleatorio está prendido)."""
    with lock:
        quieto = sala.now is None and not sala.queue
    if quieto:
        advance(sala, {"finished": None})


def set_shuffle(sala, body):
    if not director:
        raise ApiError(409, "El modo aleatorio usa las canciones guardadas: sólo anda en modo mpv")
    on = bool(body.get("on"))
    with lock:
        sala.shuffle = on
        bump(sala)
        if not sala.principal:
            guardar_salas()
    if sala.principal:
        guardar_ajustes(aleatorio=on)
    if on:
        arrancar_al_azar(sala)


def skip(sala):
    """⏭ sin saber cuál está sonando (lo usan los botones físicos)."""
    with lock:
        now_id = sala.now["id"] if sala.now else None
    if now_id:
        advance(sala, {"finished": now_id})


def previous(sala):
    """⏮ como en cualquier reproductor: si la canción va por más de 5 s, vuelve a empezar;
    si no, pasa a la anterior (y la actual vuelve a quedar primera en la cola)."""
    with lock:
        now, t = sala.now, progreso_ahora(sala)["t"]
    if now and t > 5 and director:
        if sala.principal:
            director.mpv.desde_el_principio()
        with lock:
            medir(sala.progress, 0)
            bump(sala)
        return
    with lock:
        if not sala.history:
            return
        anterior = sala.history.pop()
        if sala.now:
            sala.queue.insert(0, sala.now)
        sala.now = anterior
        sala.progress = {"t": 0, "d": 0, "playing": False}
        sala.trabajo, sala.lista, sala.reanudar = None, False, None
        bump(sala)


def pause(sala):
    """⏯. Si no suena nada y el aleatorio está prendido, arranca una al azar (p. ej. ⏯ en la
    botonera a la mañana)."""
    with lock:
        nada = sala.now is None and sala.shuffle
    if nada:
        return arrancar_al_azar(sala)
    if sala.principal:
        if not director:
            raise ApiError(409, "En modo navegador, pausa y volumen se manejan desde /player")
        return director.mpv.alternar_pausa()
    with lock:   # sin parlante: la pausa es del reloj (Director.mover la aplica)
        sala.pausada = not sala.pausada
        bump(sala)


def move_first(sala, body):
    item_id = str(body.get("id", ""))
    with lock:
        sala.queue.sort(key=lambda i: i["id"] != item_id)  # sort estable: sólo sube esa
        bump(sala)


def move_to(sala, body):
    """Mueve una canción de la cola a la posición `to` (0 = la próxima)."""
    item_id = str(body.get("id", ""))
    try:
        to = int(body.get("to", 0))
    except (TypeError, ValueError):
        raise ApiError(400, "Posición inválida")
    with lock:
        q = sala.queue
        idx = next((i for i, x in enumerate(q) if x["id"] == item_id), None)
        if idx is None:
            return
        item = q.pop(idx)
        q.insert(max(0, min(len(q), to)), item)
        bump(sala)


def mezclar_cola(sala):
    """🔀 mezclar: las próximas quedan en otro orden al azar (una vez; lo que pidan después va al final)."""
    with lock:
        q = sala.queue
        if len(q) < 2:
            return
        antes = [i["id"] for i in q]
        while [i["id"] for i in q] == antes:   # que se note: nunca el mismo orden
            random.shuffle(q)
        bump(sala)


def pin_valido(ip, pin, sala):
    """Chequea el PIN: el de la rockola (vale en todas las salas) o el de la sala. Después de 5
    intentos fallidos, bloquea esa IP 5 minutos."""
    ahora = time.time()
    with lock:
        n, hasta = fallos_pin.get(ip, (0, 0))
        if hasta > ahora:
            raise ApiError(429, "Demasiados intentos con PIN incorrecto. Esperá unos minutos.")
        if (PIN and hmac.compare_digest(pin.encode(), PIN.encode())) or (
                sala.pin and hmac.compare_digest(_hash_pin(pin, sala.sal), sala.pin)):
            fallos_pin.pop(ip, None)
            return True
        n += 1
        fallos_pin[ip] = (0, ahora + 300) if n >= 5 else (n, 0)
        return False


def audio_sonando(vid):
    """📻 radio: el archivo de la que está sonando (en alguna sala), para que la página la toque
    a la par (la va siguiendo con el estado: por dónde va, pausa, ⏭). Sólo esa: la radio pasa
    lo que suena, no es para bajarse las guardadas."""
    if not director:
        raise ApiError(409, "La radio anda sólo en modo mpv")
    with lock:
        suena = any(s.now and s.now["videoId"] == vid for s in salas.values())
    if not suena:
        raise ApiError(404, "Esa no es la que está sonando")
    ruta = director.descargas.ruta(vid)
    if not ruta:
        raise ApiError(404, "Todavía se está bajando")
    return ruta


def set_progress(body):
    """Modo navegador: el reproductor web informa por dónde va."""
    sala = salas[PRINCIPAL]
    with lock:
        playing = bool(body.get("playing"))
        was_playing = sala.progress["playing"]
        sala.progress = {"t": float(body.get("t") or 0),
                         "d": float(body.get("d") or 0),
                         "playing": playing, "at": time.monotonic()}
        if playing != was_playing:  # pausa / reanuda: avisar a todos
            bump(sala)


# ------------------------------------------------------------ salas de tv (📺)
# La cuenta de cada sala (el video, su reloj, la cola, el chat) la lleva tele.py. Acá, lo que es
# hablar con YouTube y lo que toca a las otras salas.

def _recordar_video(v):
    if len(videos) > 5000:
        videos.clear()
    videos[v["videoId"]] = v
    return v


def video(video_id):
    """Los datos de un video para una sala de tv, o None si no existe. No es `lookup`, que es para
    canciones: acá importa si es una transmisión en vivo (`live`: va sin duración, y las páginas no
    la llevan a la par por segundo) y si no deja verse fuera de YouTube."""
    r = yt().get_song(video_id) or {}
    d = r.get("videoDetails") or {}
    if d.get("videoId") != video_id:
        return None
    if d.get("isPrivate"):
        raise ApiError(400, "Ese video es privado")
    if (r.get("playabilityStatus") or {}).get("playableInEmbed") is False:
        raise ApiError(400, "Ese video no deja verse fuera de YouTube")
    largo = int(d.get("lengthSeconds") or 0)
    # En vivo: lo dice, no trae duración o trae una que no puede ser (una transmisión que ya
    # terminó queda guardada con 12 horas como mucho).
    vivo = bool(d.get("isLive")) or not largo or (bool(d.get("isLiveContent")) and largo > 12 * 3600)
    return _recordar_video({
        "videoId": video_id,
        "title": d.get("title", ""),
        "artist": d.get("author", ""),
        "duration": 0 if vivo else largo,
        "thumb": _thumb((d.get("thumbnail") or {}).get("thumbnails")),
        "live": vivo,
    })


def buscar_videos(q):
    """Para una sala de tv: videos de YouTube (un link trae ese video). `search` busca canciones en
    YouTube Music; acá sirve cualquier cosa (una charla, un resumen, un trailer), así que se busca
    en YouTube, con yt-dlp: sólo la lista de resultados, sin bajar nada."""
    m = URL_ID.search(q)
    if m:
        v = video(m.group(1))
        return [v] if v else []
    if PLAYLIST_ID.search(q):
        raise ApiError(400, "Eso es una playlist: pegá el link de un video")
    try:
        import yt_dlp   # import tardío: recién hace falta al buscar
    except ImportError:
        raise ApiError(409, "Para buscar videos hace falta yt-dlp. Mientras, pegá el link del video")
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "socket_timeout": 10}
    with yt_dlp.YoutubeDL(opts) as ydl:
        r = ydl.extract_info(f"ytsearch{MAX_VIDEOS}:{q}", download=False) or {}
    out = []
    for e in r.get("entries") or []:
        vid = str((e or {}).get("id") or "")
        if not VIDEO_ID.match(vid):
            continue
        vivo = e.get("live_status") in ("is_live", "is_upcoming") or not e.get("duration")
        out.append(_recordar_video({
            "videoId": vid,
            "title": e.get("title") or "",
            "artist": e.get("channel") or e.get("uploader") or "",
            "duration": 0 if vivo else int(e["duration"]),
            "thumb": _thumb(e.get("thumbnails")) or f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            "live": vivo,
        }))
    return out


def video_pedido(vid):
    """Los datos de un video que quieren poner en una sala de tv. Si no se puede, ApiError."""
    if not VIDEO_ID.match(vid):
        raise ApiError(400, "Video inválido")
    v = videos.get(vid) or video(vid)   # (los que salieron en una búsqueda ya se conocen)
    if not v:
        raise ApiError(404, "No encontré ese video")
    return v


def crear_tele(body):
    """Una sala de tv nueva. Como las otras, la crea cualquiera; su PIN (o el de la rockola) es
    para lo que no es de todos: sacar o adelantar un video de otro, y borrarla."""
    nombre, pin = _nombre_y_pin(body)
    sal = uuid.uuid4().hex
    room = teles.crear(_slug(nombre), nombre, _hash_pin(pin, sal), sal)
    with lock:
        bump()   # todas las de música: que aparezca en la lista
    return room


def borrar_tele(sala):
    teles.borrar(sala)
    with lock:
        bump()


def estado_tv(sala, client="", since=None, viendo=False):
    """El estado de una sala de tv para su página, con las salas de música (para el menú)."""
    e = teles.estado(sala, client, since, viendo)
    with lock:
        return dict(e, rooms=[s.publica() for s in salas.values()], mode=state["mode"])


# ------------------------------------------------------------- estadísticas

TODAS = "todas"   # ?sala=todas en el recap: todas las salas juntas


def recap(sid, periodo, persona):
    """El recap de una sala (o de todas) en un período: "hoy", "7d", "30d", un año o "todo" (sin
    nada: el año de hoy). Con `persona`, sólo lo que pidió esa persona."""
    from estadisticas import rango

    if not stats:
        raise ApiError(409, "Las estadísticas no andan: mirá los avisos en la consola de la rockola")
    periodo = periodo or time.strftime("%Y")
    try:
        desde, hasta = rango(periodo)
    except ValueError:
        raise ApiError(400, "Período inválido")
    with lock:
        if sid == TODAS:
            sala, ids = None, list(salas)
        else:
            sala = sala_de(sid)
            ids = [sala.id]
        rooms = [s.publica() for s in salas.values()]
    try:
        r = stats.recap(ids, desde, hasta, " ".join(persona.split())[:30] or None)
    except sqlite3.Error as e:
        print(f"  ⚠ no pude leer las estadísticas: {e}", flush=True)
        raise ApiError(500, "No pude leer las estadísticas")
    return dict(r, period=periodo, room=sala.publica() if sala else None, rooms=rooms, mode=state["mode"])


# --------------------------------------------------------- director (mpv)

def datos_de(item, sala=None):
    """Lo que se guarda de una canción junto con su audio (y la sala que la pide)."""
    d = {k: item.get(k) for k in ("title", "artist", "duration", "thumb", "album")}
    return dict(d, sala=sala) if sala else d


class Director:
    """Hace que mpv toque lo que dice la sala principal, y lleva las salas sin parlante.

    Es el único que le da órdenes de reproducción a mpv: mira el estado cada vez
    que cambia y lo "reconcilia" (cargar la canción nueva, frenar si no hay nada,
    pre-descargar las que vienen, borrar archivos viejos). En las salas sin parlante
    hace lo mismo con un reloj en lugar de mpv (ver `mover`).
    """

    def __init__(self):
        from reproductor import Descargas, Mpv
        args = list(MPV_ARGS)
        if NORMALIZAR:
            args.append("--af=lavfi=[dynaudnorm=f=500:g=31:p=0.95]")
        self.descargas = Descargas(carpeta() / "canciones", limite=GUARDAR_GB * 1024 ** 3,
                                   al_guardar=self.al_guardar, sala_vieja=PRINCIPAL)
        self.mpv = Mpv(self.al_terminar, self.al_cambiar, VOLUME, args, al_reiniciar=self.al_reiniciar)
        self.mpv.iniciar()
        self.pausado = False
        self.ultimo_error = None
        with lock:
            state["volume"] = VOLUME

    @property
    def principal(self):
        return salas[PRINCIPAL]

    def arrancar(self):
        threading.Thread(target=self.bucle, daemon=True, name="director").start()
        threading.Thread(target=self.bucle_progreso, daemon=True, name="progreso").start()
        threading.Thread(target=self.bucle_predescarga, daemon=True, name="predescarga").start()
        threading.Thread(target=self.bucle_albumes, daemon=True, name="albumes").start()
        threading.Thread(target=self.bucle_guardar, daemon=True, name="guardar").start()
        threading.Thread(target=self.bucle_salas, daemon=True, name="salas").start()

    def cerrar(self):
        self.mpv.cerrar()

    # --- eventos de mpv (llegan desde el hilo lector del socket: nada lento acá)

    def al_terminar(self, item_id, error):
        p = self.principal
        with lock:
            now = p.now
            titulo = now["title"] if now and now["id"] == item_id else "?"
        # Primero avanzar y después avisar: si no, el aviso despierta a la pre-descarga
        # con la canción fallida todavía como actual, y la vuelve a bajar.
        if advance(p, {"finished": item_id}) and error:
            avisar(f"Salteada “{titulo}”: {error}", p)

    def al_guardar(self):
        """Quedó guardada una canción: que las páginas actualicen la cuenta y la lista."""
        with lock:
            bump()
        averiguar_generos()   # (el de su artista, si todavía no se sabe)

    def al_reiniciar(self):
        """mpv se cayó y se levantó de nuevo: lo que sonaba se perdió.

        El aviso cambia el estado, así que el Director se despierta y la vuelve a cargar, en el
        segundo en que iba (el último que se midió).
        """
        p = self.principal
        with lock:
            if p.now is not None and p.reanudar is None and p.progress["t"]:
                p.reanudar = {"id": p.now["id"], "t": p.progress["t"], "pausada": self.pausado}
        avisar("El reproductor se reinició; retomando la canción", p)

    def al_cambiar(self, nombre, valor):
        p = self.principal
        with lock:
            if nombre == "pause":
                self.pausado = bool(valor)
                pr = p.progress
                sonando = not self.pausado and p.now is not None and self.mpv.pedido is not None
                if pr["playing"] != sonando:
                    medir(pr, progreso_ahora(p)["t"])   # al pausar, que quede donde iba de verdad
                    pr["playing"] = sonando
                    bump(p)
            elif nombre == "volume" and valor is not None:
                v = round(valor)
                if state["volume"] != v:
                    state["volume"] = v
                    bump(p)
            elif nombre == "duration" and valor:
                p.progress["d"] = valor

    # --- bucles

    def bucle(self):
        p = self.principal
        visto = -1
        while True:
            with changed:
                while state["version"] == visto:
                    changed.wait()
                visto = state["version"]
                now, queue = p.now, list(p.queue)
            try:
                self.reconciliar(now, queue)
                self.ultimo_error = None
            except Exception as e:
                msg = f"Problema con el reproductor: {e}"
                if msg != self.ultimo_error:
                    avisar(msg, p)
                    self.ultimo_error = msg
                time.sleep(2)
                visto = -1  # reintentar

    def bucle_predescarga(self):
        """Apenas cambia la cola, empieza a bajar la actual (ya) y, de a una y en orden, las
        que vienen (todas, o las `PREDESCARGAR` primeras): como mucho dos descargas a la vez,
        que en una Pi con poca memoria cada yt-dlp pesa. Con varias salas, las que vienen se
        bajan intercaladas: la próxima de cada sala, después la siguiente de cada una…

        Va aparte del bucle principal porque ese puede estar esperando una descarga.
        """
        visto = -1
        while True:
            with changed:
                while state["version"] == visto:
                    changed.wait()
                visto = state["version"]
                now = self.principal.now
                colas = [[(it["videoId"], datos_de(it, s.id)) for it in s.queue[:PREDESCARGAR or None]]
                         for s in salas.values()]
            despues = [c for ronda in itertools.zip_longest(*colas) for c in ronda if c]
            try:
                if now:
                    self.descargas.pedir(now["videoId"], datos_de(now, PRINCIPAL))
                self.descargas.anticipar(despues)
            except Exception as e:  # p. ej. no se pudo crear el hilo: reintentar, no morir
                print(f"  ⚠ pre-descarga: {e}", flush=True)
                time.sleep(2)
                visto = -1

    def bucle_guardar(self, olvidar=60, pausa=1):
        """Las que pidieron guardar sin ponerlas en la cola (state["saving"]): las baja de a
        una. Mientras se baja una que tiene que sonar espera, que eso va primero (y así no hay
        más de dos descargas a la vez). Las que fallan quedan `olvidar` segundos en la lista,
        con su error, para que la página lo muestre.
        """
        while True:
            with changed:
                while True:
                    ahora = time.time()
                    quedan = [g for g in state["saving"] if not g["error"] or g["hasta"] > ahora]
                    if len(quedan) != len(state["saving"]):
                        state["saving"] = quedan
                        bump()
                    sig = next((g for g in state["saving"] if not g["error"]), None)
                    if sig:
                        break
                    changed.wait(timeout=olvidar / 2)
            while any(s.loading for s in list(salas.values())):
                time.sleep(pausa)
            with lock:
                if not any(g is sig for g in state["saving"]):
                    continue   # la cancelaron mientras esperaba
                sig["bajando"] = True
                bump()
            vid, error = sig["videoId"], None
            try:
                if not self.descargas.datos(vid):   # (si estaba en la cola, capaz ya se bajó)
                    self.descargas.esperar(vid, datos=sig["datos"], timeout=600)
                    if not self.descargas.datos(vid):
                        error = "se bajó pero no quedó guardada (¿falta lugar en el disco?)"
            except Exception as e:   # ErrorDescarga, o yt-dlp con algo raro
                error = str(e)[:160] or type(e).__name__
            with lock:
                if not error:   # que quede en las guardadas de cada sala que la pidió
                    for sid in sig["salas"]:
                        if sid in salas:
                            self.descargas.sumar_a_sala(vid, sid)
                sig["bajando"] = False
                cancelada = not sig["salas"]
                if error and not cancelada:
                    sig["error"], sig["hasta"] = error, time.time() + olvidar
                else:
                    state["saving"] = [g for g in state["saving"] if g is not sig]
                bump()
            if cancelada and not error:   # la cancelaron mientras se bajaba: no queda guardada
                self.descargas.descartar(vid, en_uso())
            elif error:
                print(f"  ⚠ no pude guardar “{sig['title']}”: {error}", flush=True)

    def sonar_al_arrancar(self, espera=10, cada=5, retomar=False):
        """Recién arrancada la rockola (al prender la Raspberry, o al reiniciar el servicio,
        p. ej. después de actualizarla): si en `espera` segundos nadie pidió nada, 🔀 al azar
        y a sonar, para que no quede en silencio. Con un parlante Bluetooth (pi/bluetooth.sh)
        espera a que se conecte, aunque lo prendan al rato; si no hay (jack, USB, HDMI), ya.

        Con `retomar`, la que sonaba antes de apagarse quedó cargada en pausa (ver retomar_colas):
        en lugar de 🔀, sigue ésa, con el parlante listo."""
        p = self.principal
        time.sleep(espera)
        with lock:
            if not retomar and (p.now is not None or p.queue):
                return   # ya pidieron algo
            if not retomar:
                p.shuffle = True
                bump(p)
        if not retomar:
            guardar_ajustes(aleatorio=True)
        mac = parlante_bluetooth()
        if mac and not parlante_listo(mac):
            print("  ⏯ Espero que se conecte el parlante Bluetooth para arrancar", flush=True)
            while not parlante_listo(mac):
                time.sleep(cada)
        if retomar:
            with lock:
                if p.reanudar and p.now and p.reanudar["id"] == p.now["id"]:
                    p.reanudar["pausada"], seguir = False, False   # todavía no cargó: que arranque sonando
                else:
                    seguir = p.now is not None and self.pausado
            if seguir:
                print("  ⏯ Sigue la que estaba sonando antes de apagarse", flush=True)
                self.mpv.fijar_pausa(False)
            return
        with lock:
            seguir = p.shuffle   # si mientras tanto lo apagaron desde la web, no
        if not seguir:
            return
        if self.descargas.resumen(PRINCIPAL)["songs"]:
            print("  🔀 Al azar: arranca la música", flush=True)
        else:
            print("  🔀 Al azar, pero todavía no hay canciones guardadas: suena cuando pidan algo", flush=True)
        arrancar_al_azar(p)

    def bucle_albumes(self, espera=20, pausa=2):
        """Las canciones que guardó una versión que no anotaba el álbum: se lo pregunta a
        YouTube Music, de a una y sin apuro (arranca un rato después de prender).

        Si YouTube no responde, prueba más tarde; una que falla 3 veces queda para la
        próxima vez que arranque.
        """
        time.sleep(espera)
        fallos, hechas = {}, 0
        while faltan := [v for v in self.descargas.sin_album() if fallos.get(v, 0) < 3]:
            for vid in faltan:
                try:
                    album = album_de(vid)
                except Exception as e:   # sin internet, YouTube no contesta…
                    fallos[vid] = fallos.get(vid, 0) + 1
                    if len(fallos) == 1 and fallos[vid] == 1:
                        print(f"  ⚠ no pude averiguar álbumes (pruebo más tarde): {e}", flush=True)
                    time.sleep(pausa * 30)
                    continue
                self.descargas.completar_album(vid, album)
                hechas += 1
                if hechas % 20 == 0:   # que la lista se vaya acomodando, sin avisar por cada una
                    with lock:
                        bump()
                time.sleep(pausa)
        if hechas:
            with lock:
                bump()

    def bucle_progreso(self):
        from reproductor import ErrorMpv

        p = self.principal
        sin_archivo = 0
        while True:
            time.sleep(1)
            pedido = self.mpv.pedido
            if pedido is None:
                sin_archivo = 0
                continue
            try:
                if not self.mpv.esta_vivo():
                    # mpv se cayó o se colgó: levantarlo (al_reiniciar hace que el
                    # Director vuelva a cargar la canción, en vez de saltearla).
                    self.mpv.asegurar()
                    continue
                t = self.mpv.posicion()
            except ErrorMpv:
                continue  # no respondió: si se repite, Mpv lo da por colgado y se reinicia
            if t is None:
                # Perro guardián: hay una canción pedida pero mpv no tiene nada cargado
                # hace rato (se perdió el evento de fin).
                sin_archivo += 1
                if sin_archivo >= 5:
                    sin_archivo = 0
                    self.al_terminar(pedido, None)
                continue
            sin_archivo = 0
            with lock:
                if p.now and p.now["id"] == pedido:
                    medir(p.progress, t)

    # --- reconciliar: hacer que suene lo que dice la sala principal

    def reconciliar(self, now, queue):
        p = self.principal
        if now is None:
            if queue:  # hay cola pero nada sonando: arrancar
                advance(p, {"finished": None})
                return
            if self.mpv.pedido is not None:
                self.mpv.detener()
            with lock:
                if p.loading:
                    p.loading = False
                    bump(p)
            self.descargas.conservar(en_uso())
            return

        # Las que vienen se bajan solas (bucle_predescarga): sin silencio entre temas.
        if now["id"] != self.mpv.pedido:
            self.cargar(now)
        # Se guardan la que suena, las que vienen y la anterior, en todas las salas.
        self.descargas.conservar(en_uso())

    def vigente(self, item_id):
        p = self.principal
        with lock:
            return p.now is not None and p.now["id"] == item_id

    def cargar(self, now):
        from reproductor import ErrorDescarga

        p = self.principal
        with lock:   # si es la que sonaba antes de reiniciar: en el segundo en que iba (y en pausa, si estaba)
            r = p.reanudar if p.reanudar and p.reanudar["id"] == now["id"] else None
        desde, pausada = (r["t"], r["pausada"]) if r else (0, False)
        if not self.descargas.lista(now["videoId"]):
            if self.mpv.pedido is not None:
                self.mpv.detener()  # que no siga sonando la anterior mientras baja
            with lock:
                p.loading = True
                bump(p)
        sonando = False
        try:
            try:
                # Si la saltean mientras baja, deja de esperar (devuelve None).
                ruta = self.descargas.esperar(now["videoId"], lambda: self.vigente(now["id"]),
                                              datos=datos_de(now, PRINCIPAL))
            except ErrorDescarga as e:
                if advance(p, {"finished": now["id"]}):
                    avisar(f"Salteada “{now['title']}”: {e}", p)
                return
            if ruta is None or not self.vigente(now["id"]):
                return
            sonando = self.mpv.reproducir(now["id"], ruta, desde=desde, pausada=pausada)
            if sonando:
                self.descargas.usada(now["videoId"], PRINCIPAL)
        finally:  # pase lo que pase, que no quede "cargando…" colgado
            with lock:
                cambio = p.loading
                p.loading = False
                if sonando and p.now is not None and p.now["id"] == now["id"]:
                    p.progress = {"t": desde, "d": now["duration"], "playing": not pausada, "at": time.monotonic()}
                    if p.reanudar is r:
                        p.reanudar = None
                    cambio = True
                if cambio:
                    bump(p)

    # --- salas sin parlante

    def bucle_salas(self, paso=0.5):
        """Cada medio segundo (o apenas cambia algo), mueve las salas sin parlante y da por
        apagadas las 📻 radios de las que no se sabe nada hace rato."""
        while True:
            with changed:
                changed.wait(timeout=paso)
                todas = list(salas.values())
            for sala in todas:
                try:
                    self.mover(sala)
                except Exception as e:   # no debería pasar: que no se muera el hilo
                    print(f"  ⚠ sala {sala.nombre}: {e!r}", flush=True)
                    time.sleep(1)

    def mover(self, sala):
        """Una sala sin parlante: lo que en la principal hace mpv (bajar la que toca, que pase
        el tiempo, pasar a la siguiente al terminar), con un reloj. Suena sólo mientras alguien
        la escucha con 📻 radio: si no, queda en espera, donde iba. Así lo que piden no se
        pierde sonando para nadie."""
        ahora = time.monotonic()
        with lock:
            if salas.get(sala.id) is not sala:
                return   # la borraron
            vencidas = [c for c, t in sala.oyentes.items() if ahora - t >= OYENTE_VENCE]
            for c in vencidas:
                del sala.oyentes[c]
            if vencidas:
                bump(sala)
            if sala.principal:
                return
            escuchan, now = bool(sala.oyentes), sala.now
            if now is None:
                if sala.loading:
                    sala.loading = False
                    bump(sala)
                arrancar = bool(sala.queue) or (sala.shuffle and escuchan)
        if now is None:
            if arrancar:
                advance(sala, {"finished": None})
            return

        trabajo = sala.trabajo
        if trabajo is None or trabajo[0] != now["id"]:
            # (si ya está bajada, no la baja: el trabajo ya viene listo)
            trabajo = (now["id"], self.descargas.pedir(now["videoId"], datos_de(now, sala.id)))
            with lock:
                if sala.now is not now:
                    return   # cambió mientras tanto: la próxima vuelta
                sala.trabajo, sala.lista = trabajo, False
        t = trabajo[1]
        if not t.listo.is_set():
            with lock:
                if sala.now is now and not sala.loading:
                    sala.loading = True
                    bump(sala)
            return
        if t.error or not t.ruta:
            if advance(sala, {"finished": now["id"]}):
                avisar(f"Salteada “{now['title']}”: {t.error or 'no se pudo bajar'}", sala)
            return

        estrena = cambio = False
        with lock:
            if sala.now is not now:
                return   # cambió mientras tanto: la próxima vuelta
            p = sala.progress
            if not sala.lista:   # recién bajada: arranca desde el principio (o donde iba, si se retoma)
                r, sala.reanudar = sala.reanudar, None
                desde = r["t"] if r and r["id"] == now["id"] else 0
                sala.lista, sala.loading, estrena = True, False, True
                sala.progress = p = {"t": desde, "d": now["duration"], "playing": False, "at": ahora}
            suena = escuchan and not sala.pausada
            if p["playing"] != suena:
                medir(p, progreso_ahora(sala)["t"])
                p["playing"] = suena
                cambio = True
            termino = p["playing"] and progreso_ahora(sala)["t"] >= (p["d"] or now["duration"])
            if estrena or cambio:
                bump(sala)
        if estrena:   # (y si era de otra sala, ahora es también de esta)
            self.descargas.usada(now["videoId"], sala.id)
        if termino:
            advance(sala, {"finished": now["id"]})
            self.descargas.conservar(en_uso())


# ---------------------------------------------------------------- utilidades

ARCHIVO_PARLANTE = Path.home() / ".config" / "rockola" / "parlante-bluetooth"   # lo escribe pi/bluetooth.sh


def recien_prendida(minutos=10, uptime=Path("/proc/uptime")):
    """Si la máquina se prendió hace poco (al prender la Raspberry, la rockola arranca a sonar)."""
    try:
        return float(uptime.read_text().split()[0]) < minutos * 60
    except (OSError, ValueError, IndexError):
        return False


def como_servicio():
    """Si corre como servicio de systemd (en la Raspberry, pi/instalar.sh), y no a mano en una
    terminal: systemd le pone INVOCATION_ID a lo que arranca."""
    return bool(os.environ.get("INVOCATION_ID"))


def reinicio_diario(minutos=15):
    """Si este arranque es el reinicio diario de las 6 de la mañana (rockola-reinicio.timer, de
    pi/instalar.sh): se fija si ese servicio corrió hace poco. Ahí no tiene que arrancar a
    sonar: la oficina está vacía."""
    try:
        r = subprocess.run(["systemctl", "--user", "show", "rockola-reinicio.service",
                            "--property=ExecMainStartTimestampMonotonic,InactiveExitTimestampMonotonic"],
                           capture_output=True, text=True, timeout=10)
        corrio = max((int(v) for v in re.findall(r"=(\d+)", r.stdout)), default=0) / 1e6
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    # Los tiempos "Monotonic" de systemd son los de time.monotonic() (CLOCK_MONOTONIC), en µs.
    return corrio > 0 and 0 <= time.monotonic() - corrio < minutos * 60


def parlante_bluetooth():
    """La MAC del parlante Bluetooth elegido con pi/bluetooth.sh, o None si no hay."""
    try:
        mac = ARCHIVO_PARLANTE.read_text().strip().upper()
    except OSError:
        return None
    return mac if re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", mac) else None


def parlante_listo(mac):
    """Si el parlante Bluetooth ya es una salida de audio: conectado y visto por PipeWire (lo
    mismo que busca pi/salidas.py). Sin pw-dump no hay cómo saberlo: se lo da por listo."""
    try:
        objs = json.loads(subprocess.run(["pw-dump"], capture_output=True, timeout=15).stdout or b"[]")
    except FileNotFoundError:
        return True
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    nombre = "BLUEZ_OUTPUT." + mac.replace(":", "_")
    for o in objs if isinstance(objs, list) else []:
        props = ((o.get("info") or {}).get("props") or {}) if isinstance(o, dict) else {}
        if (o.get("type") == "PipeWire:Interface:Node" and props.get("media.class") == "Audio/Sink"
                and str(props.get("node.name", "")).upper().startswith(nombre)):
            return True
    return False


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no envía nada, sólo elige la interfaz
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def url_publica():
    # Se calcula cada vez: en una Raspberry el wifi puede conectarse después de arrancar.
    return f"http://{IP_FIJA or lan_ip()}:{PORT}/"


def qr_svg(texto):
    import qrcode
    qr = qrcode.QRCode(border=2)
    qr.add_data(texto)
    qr.make(fit=True)
    m = qr.get_matrix()
    n = len(m)
    d = "".join(f"M{x},{y}h1v1h-1z" for y, fila in enumerate(m) for x, v in enumerate(fila) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{d}" fill="#000"/></svg>')


# ---------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "Rockola/2.0"
    timeout = 30  # una conexión que no manda nada no se queda con un hilo para siempre

    @property
    def is_local(self):
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return False
        # Contra "DNS rebinding": una página maliciosa abierta en esta máquina podría
        # hablarle a localhost con otro nombre de host. Sólo vale si dice localhost.
        host = (self.headers.get("Host") or "").lower()
        host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        return host.strip("[]") in ("", "localhost", "127.0.0.1", "::1")

    def es_admin(self, sala):
        """Puede controlar la música de `sala`: desde esta máquina, o desde la web con el PIN
        (el de la rockola, o el de la sala)."""
        if self.is_local:
            return True
        pin = (self.headers.get("X-Pin") or "").strip()
        return bool(pin) and pin_valido(self.client_address[0], pin, sala)

    def send_bytes(self, data, ctype, code=200, cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, code=200):
        self.send_bytes(json.dumps(obj).encode(), "application/json; charset=utf-8", code)

    def send_page(self, name):
        self.send_bytes((STATIC / name).read_bytes(), "text/html; charset=utf-8")

    def send_audio(self, ruta):
        """Un archivo de audio, entero o el pedazo que diga Range: así el navegador puede
        arrancar por cualquier parte de la canción."""
        with open(ruta, "rb") as f:
            tam = os.fstat(f.fileno()).st_size
            desde, hasta = 0, tam - 1
            m = RANGO.fullmatch((self.headers.get("Range") or "").strip())
            parcial = bool(m and (m[1] or m[2]))
            if parcial:
                if m[1]:
                    desde = int(m[1])
                    hasta = min(int(m[2]), tam - 1) if m[2] else tam - 1
                else:   # "bytes=-500": los últimos 500
                    desde = max(0, tam - int(m[2]))
                if desde > hasta:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{tam}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            self.send_response(206 if parcial else 200)
            self.send_header("Content-Type", TIPOS_AUDIO.get(Path(ruta).suffix.lower(), "application/octet-stream"))
            self.send_header("Content-Length", str(hasta - desde + 1))
            self.send_header("Accept-Ranges", "bytes")
            if parcial:
                self.send_header("Content-Range", f"bytes {desde}-{hasta}/{tam}")
            self.send_header("Cache-Control", "private, max-age=3600")   # el audio de un tema no cambia
            self.end_headers()
            if hasta >= desde:
                self.connection.sendfile(f, desde, hasta - desde + 1)

    def redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_body(self):
        # Exigir JSON obliga al navegador a hacer preflight CORS, así otra
        # página web no puede mandar POSTs a la rockola a tus espaldas.
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise ApiError(415, "Se espera JSON")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "Content-Length inválido")
        if n < 0 or n > 10_000:
            raise ApiError(413, "Demasiado grande")
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            raise ApiError(400, "JSON inválido")

    def handle_errors(self, fn):
        try:
            fn()
        except (ApiError, ErrorLista, ErrorTele) as e:
            self.send_json({"error": e.msg}, e.code)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            # El cliente se fue (p. ej. cerró la pestaña durante un long-poll), o dejó de leer:
            # el navegador, con el audio de la radio, cuando ya tiene bastante.
            pass
        except Exception as e:  # p. ej. YouTube no respondió
            self.log_error("error: %r", e)
            self.send_json({"error": "Algo falló hablando con YouTube. Probá de nuevo."}, 502)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)

        def route():
            if u.path in ("/", "/recap"):   # (/recap: la misma página, abierta en el 📊 recap)
                return self.send_page("index.html")
            if u.path == "/pantalla":
                return self.send_page("pantalla.html")
            if u.path == "/tv":
                return self.send_page("tv.html")
            if u.path == "/player":
                if state["mode"] == "mpv":
                    return self.redirect("/pantalla")  # la música ya suena en el servidor
                if not self.is_local:
                    raise ApiError(403, "El reproductor sólo se abre desde la PC que pone la música")
                return self.send_page("player.html")
            if u.path in ("/favicon.svg", "/favicon.ico"):   # .ico: los que lo piden igual
                return self.send_bytes((STATIC / "favicon.svg").read_bytes(), "image/svg+xml", cache=True)
            if u.path == "/api/state":
                since = qs.get("since", [""])[0]
                return self.send_json(public_state(
                    sala_de(qs.get("sala", [""])[0]),
                    qs.get("client", [""])[0][:64],
                    int(since) if since.isdigit() else None,
                    radio=qs.get("radio", [""])[0] == "1"))
            if u.path == "/api/tv/state":
                since = qs.get("since", [""])[0]
                return self.send_json(estado_tv(
                    teles.sala(qs.get("sala", [""])[0]),
                    qs.get("client", [""])[0][:64],
                    int(since) if since.isdigit() else None,
                    viendo=qs.get("viendo", [""])[0] == "1"))
            if u.path == "/api/tv/rooms":   # para elegir una (la página /tv, sin sala)
                with lock:
                    rooms = [s.publica() for s in salas.values()]
                return self.send_json({"tvs": teles.publicas(), "rooms": rooms, "mode": state["mode"]})
            if u.path == "/api/tv/search":
                q = qs.get("q", [""])[0].strip()[:200]
                return self.send_json(buscar_videos(q) if q else [])
            if u.path == "/api/search":
                q = qs.get("q", [""])[0].strip()[:200]
                res = search(q) if q else []
                if director:   # cuáles ya están en las guardadas de la sala (copias: no tocar las de meta_cache)
                    sid = sala_de(qs.get("sala", [""])[0]).id
                    res = [dict(s, saved=director.descargas.en_sala(s["videoId"], sid)) for s in res]
                return self.send_json(res)
            if u.path == "/api/albums":   # los de YouTube: para verlos, cargarlos enteros o ⬇ guardarlos
                q = qs.get("q", [""])[0].strip()[:200]
                sid = sala_de(qs.get("sala", [""])[0]).id
                res = buscar_albumes(q) if q else []
                if director:   # cuántos temas de cada uno ya están en las guardadas de la sala
                    res = [dict(a, saved=director.descargas.del_album(a["title"], a["artist"], sid)) for a in res]
                return self.send_json(res)
            if u.path == "/api/album":   # los temas de uno de esos álbumes
                bid = qs.get("browseId", [""])[0]
                if not ALBUM_ID.match(bid):
                    raise ApiError(400, "Álbum inválido")
                al = dict(get_album(bid))
                if director:
                    sid = sala_de(qs.get("sala", [""])[0]).id
                    al["songs"] = [dict(c, saved=director.descargas.en_sala(c["videoId"], sid)) for c in al["songs"]]
                return self.send_json(al)
            if u.path == "/api/lists":   # las ♫ playlists de la sala (sin sus temas)
                if not listas:
                    raise ApiError(409, "No hay playlists")
                return self.send_json(listas.de_sala(sala_de(qs.get("sala", [""])[0]).id,
                                                     qs.get("client", [""])[0][:64]))
            if u.path == "/api/list":   # una, con sus temas
                if not listas:
                    raise ApiError(409, "No hay playlists")
                return self.send_json(listas.una(sala_de(qs.get("sala", [""])[0]).id, qs.get("id", [""])[0][:20],
                                                 qs.get("client", [""])[0][:64]))
            if u.path == "/api/playlist":
                return self.send_json(playlist_info(qs.get("q", [""])[0][:300]))
            if u.path.startswith("/api/audio/"):   # 📻 radio
                return self.send_audio(audio_sonando(u.path.removeprefix("/api/audio/")))
            if u.path == "/api/lyrics":
                vid = qs.get("videoId", [""])[0]
                if not VIDEO_ID.match(vid):
                    raise ApiError(400, "Canción inválida")
                return self.send_json(letra(vid))
            if u.path == "/api/library":
                if not director:
                    raise ApiError(409, "Las canciones guardadas sólo existen en modo mpv")
                desde, n = qs.get("desde", ["0"])[0], qs.get("n", ["30"])[0]
                desde, n = int(desde) if desde.isdigit() else 0, min(500, int(n)) if n.isdigit() else 30
                vista, sid = qs.get("vista", [""])[0], sala_de(qs.get("sala", [""])[0]).id
                if vista == "generos":   # los géneros, con cuántas tiene cada uno
                    de, faltan = _generos_de(sid)
                    return self.send_json(dict(director.descargas.generos(de, desde, n, sala=sid), pending=faltan))
                if vista == "genero":    # las de uno
                    return self.send_json(genero_guardado(sid, qs.get("genero", [""])[0][:80]))
                ver = director.descargas.albumes if vista == "albumes" else director.descargas.listado
                return self.send_json(ver(qs.get("q", [""])[0][:100], desde, n, sala=sid))
            if u.path == "/api/stats":   # el recap
                return self.send_json(recap(qs.get("sala", [""])[0], qs.get("periodo", [""])[0][:10],
                                            qs.get("persona", [""])[0]))
            if u.path == "/api/info":
                return self.send_json({"url": url_publica(), "mode": state["mode"],
                                       "pin": bool(PIN)})
            if u.path == "/api/qr.svg":
                try:
                    svg = qr_svg(url_publica())
                except ImportError:
                    raise ApiError(404, "Falta el paquete qrcode")
                return self.send_bytes(svg.encode(), "image/svg+xml")
            raise ApiError(404, "No existe")

        self.handle_errors(route)

    def do_POST(self):
        path = urlparse(self.path).path

        def route():
            body = self.read_body()
            if path.startswith("/api/tv/"):
                return self.tv(path.removeprefix("/api/tv/"), body)
            if path == "/api/rooms":   # crear una sala (cualquiera; su PIN es para controlarla)
                return self.send_json({"room": crear_sala(body)})
            sala = sala_de(body.get("sala"))
            client = body.get("client")
            if path == "/api/queue":
                return self.send_json({"item": add_song(sala, body)})
            if path == "/api/download":   # guardarla sin ponerla en la cola
                listo = save_song(sala, body)
                return self.send_json({"done": listo, "state": public_state(sala, client)})
            if path == "/api/download-album":   # lo mismo con un álbum entero
                r = save_album(sala, body)
                return self.send_json(dict(r, state=public_state(sala, client)))
            if path == "/api/remove":
                remove_song(sala, body, self.es_admin(sala))
                return self.send_json(public_state(sala, client))
            if path == "/api/download-cancel":   # ✕ en las que esperan para bajarse
                n = cancelar_guardado(sala, body, self.es_admin(sala))
                return self.send_json({"cancelled": n, "state": public_state(sala, client)})
            if path == "/api/chat":
                chatear(sala, body)
                return self.send_json(public_state(sala, client))
            if path == "/api/radio":   # 📻 prendida o apagada (para saber si alguien escucha)
                set_radio(sala, body)
                return self.send_json(public_state(sala, client))
            if path.startswith("/api/list-"):   # ♫ armar playlists: cualquiera (las cambia quien la armó, o con el PIN)
                if not listas:
                    raise ApiError(409, "No hay playlists")
                admin = self.es_admin(sala)
                r = sumar_a_lista(sala, body, admin) if path == "/api/list-add" else editar_lista(sala, path, body, admin)
                return self.send_json(dict(r, state=public_state(sala, client)))
            if path == "/api/progress":  # lo usa el reproductor web, que corre en esta máquina
                if not self.is_local:
                    raise ApiError(403, "Sólo desde la PC que pone la música")
                if state["mode"] != "navegador":
                    raise ApiError(409, "En modo mpv el progreso lo maneja el servidor")
                set_progress(body)
                return self.send_json(public_state(sala))
            # Lo que sigue: desde esta máquina, o desde la web con el PIN (de la rockola o de la sala).
            if not self.es_admin(sala):
                raise ApiError(403, "Para controlar la música hace falta el PIN")
            if path == "/api/admin":
                return self.send_json({"ok": True, "mode": state["mode"]})
            if path in ("/api/load", "/api/playlist"):   # una colección entera a la cola (/api/playlist: un link)
                r = cargar_entera(sala, body if path == "/api/load" else dict(body, **{"from": "youtube"}))
                return self.send_json(dict(r, state=public_state(sala, client)))
            if path == "/api/room-delete":
                borrar_sala(sala)
                return self.send_json({"ok": True})
            if path == "/api/library-remove":   # 🗑 una guardada, a mano
                texto = quitar_guardada(sala, body)
                return self.send_json({"text": texto, "state": public_state(sala, client)})
            if path == "/api/play-now":
                play_now(sala, body)
            elif path == "/api/next":
                advance(sala, body)
            elif path == "/api/skip":
                skip(sala)
            elif path == "/api/prev":
                previous(sala)
            elif path == "/api/first":
                move_first(sala, body)
            elif path == "/api/move":
                move_to(sala, body)
            elif path == "/api/remove-playlist":
                remove_playlist(sala, body)
            elif path == "/api/shuffle":
                set_shuffle(sala, body)
            elif path == "/api/shuffle-queue":
                mezclar_cola(sala)
            elif path == "/api/pause":
                pause(sala)
            elif path == "/api/volume":
                if not sala.principal:
                    raise ApiError(409, "Esta sala no tiene parlante: cada uno maneja su volumen")
                if not director:
                    raise ApiError(409, "En modo navegador, pausa y volumen se manejan desde /player")
                if "value" in body:
                    director.mpv.fijar_volumen(float(body["value"]))
                else:
                    director.mpv.cambiar_volumen(float(body.get("delta") or 0))
            else:
                raise ApiError(404, "No existe")
            return self.send_json(public_state(sala, client))

        self.handle_errors(route)

    def tv(self, que, body):
        """Los pedidos de una sala de tv (📺). Poner videos, chatear y el control (pausa, ir a otro
        segundo, ⏭) son de todos; con el PIN, además, lo de la cola de otros y borrar la sala."""
        if que == "rooms":
            return self.send_json({"room": crear_tele(body)})
        sala = teles.sala(body.get("sala"))
        client = str(body.get("client", ""))[:64]
        if que == "queue":
            teles.agregar(sala, video_pedido(str(body.get("videoId", ""))), client, body.get("by"))
        elif que == "remove":
            teles.quitar(sala, body.get("id"), client, self.es_admin(sala))
        elif que == "watch":   # esa página tiene el video a la vista (o lo dejó de tener)
            teles.mirar(sala, client, bool(body.get("on")))
        elif que == "control":
            teles.control(sala, body)
        elif que == "next":
            teles.siguiente(sala, body.get("id"), client, body.get("by"))
        elif que == "chat":
            teles.chatear(sala, body)
        # Lo que sigue: desde esta máquina, o desde la web con el PIN (de la rockola o de la sala).
        elif not self.es_admin(sala):
            raise ApiError(403, "Para eso hace falta el PIN")
        elif que == "admin":
            return self.send_json({"ok": True})
        elif que == "first":
            teles.primero(sala, body.get("id"))
        elif que == "room-delete":
            borrar_tele(sala)
            return self.send_json({"ok": True})
        else:
            raise ApiError(404, "No existe")
        return self.send_json(estado_tv(sala, client))

    def log_message(self, fmt, *args):
        # No llenar la consola con los polls de estado.
        path = getattr(self, "path", "")  # no existe si la conexión venció antes del pedido
        if any(p in path for p in ("/api/state", "/api/tv/state", "/api/progress", "/api/info", "/api/qr",
                                   "/api/audio")):
            return
        super().log_message(fmt, *args)


def main():
    global director, stats, listas, teles, PORT, IP_FIJA
    ap = argparse.ArgumentParser(description="Rockola de oficina")
    ap.add_argument("--modo", choices=["mpv", "navegador"],
                    help="dónde suena la música (por defecto: mpv si está instalado)")
    ap.add_argument("--puerto", type=int)
    ap.add_argument("--ip", help="IP a mostrar en el link/QR (si detecta la red equivocada)")
    ap.add_argument("--config", type=Path, default=CONFIG, help="archivo de configuración")
    args = ap.parse_args()

    try:
        cargar_config(args.config)
    except (tomllib.TOMLDecodeError, ValueError) as e:
        sys.exit(f"Error en {args.config}: {e}")
    PORT = args.puerto or PORT
    IP_FIJA = args.ip or IP_FIJA
    modo = args.modo or MODO or ("mpv" if shutil.which("mpv") else "navegador")
    if modo not in ("mpv", "navegador"):
        sys.exit(f"Modo desconocido: {modo!r} (tiene que ser mpv o navegador)")
    state["mode"] = modo
    salas[PRINCIPAL].shuffle = modo == "mpv" and bool(leer_ajustes().get("aleatorio"))

    from estadisticas import Estadisticas
    try:
        stats = Estadisticas(carpeta() / "estadisticas.db")
    except (OSError, sqlite3.Error) as e:   # sin estadísticas, pero que suene
        print(f"  ⚠ no pude abrir las estadísticas ({carpeta() / 'estadisticas.db'}): {e}", flush=True)
    listas = Listas(carpeta() / "listas.json")
    teles = Teles(carpeta() / "tv.json", MAX_PER_PERSON)
    teles.arrancar()

    if modo == "mpv":
        from reproductor import ErrorMpv
        leer_salas()
        # Cada sala sigue donde estaba (la cola, y la que sonaba en el segundo en que iba). Al
        # reiniciar el servicio o a mano (p. ej. para aplicar un cambio), tal cual. En el reinicio
        # diario de las 6, la principal en pausa: la oficina está vacía. Al prender la Raspberry,
        # también en pausa, y con azar_al_prender sigue sola cuando el parlante esté listo.
        diario, prendida = reinicio_diario(), recien_prendida()
        seguia = retomar_colas(en_pausa=diario or prendida)
        try:
            director = Director()
        except ErrorMpv as e:
            sys.exit(f"Error: {e}")
        director.arrancar()
        averiguar_generos()   # (los de las guardadas que falten: se le pregunta a iTunes de a uno, sin apuro)
        # Al prender la Raspberry o reiniciar el servicio (p. ej. al actualizar), si nadie pide
        # nada: 🔀 y que suene sola. En el reinicio diario de las 6, no: la oficina está vacía.
        retomar = seguia and prendida
        if AZAR_AL_PRENDER and (director.descargas.limite or retomar) and (
                prendida or (como_servicio() and not diario)):
            threading.Thread(target=director.sonar_al_arrancar, kwargs={"retomar": retomar},
                             daemon=True, name="al-arrancar").start()
    else:
        retomar_colas()   # (lo retoma el reproductor web, que arranca donde dice el progreso)
    threading.Thread(target=bucle_colas, daemon=True, name="colas").start()

    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)

    def terminar(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminar)  # systemctl stop
    print(f"\n  🎵 Rockola andando (modo {modo})\n")
    print(f"  Compañeros:  {url_publica()}")
    if modo == "mpv":
        print(f"  Pantalla:    ./tui.sh   o   http://localhost:{PORT}/pantalla")
    else:
        print(f"  Reproductor: http://localhost:{PORT}/player")
    if PIN:
        print(f"  Controles web: con el PIN de {args.config.name}")
    else:
        print(f"  Controles web: desactivados (poné un pin en {args.config.name})")
    print()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            guardar_colas(final=True)   # antes de cerrar mpv: que quede el segundo en que iba
        except Exception as e:
            print(f"  ⚠ no pude guardar las colas: {e!r}", flush=True)
        teles.cerrar()   # que quede por dónde iba cada video
        if stats:   # (la que estaba sonando se anota cuando termine, al retomarla)
            stats.cerrar()
        if director:
            director.cerrar()


if __name__ == "__main__":
    main()
