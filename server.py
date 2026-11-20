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
import gzip
import hashlib
import hmac
import ssl
import itertools
import json
import math
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

from voz import ErrorVoz
from listas import MAX_TEMAS as MAX_TEMAS_LISTA, ErrorLista, Listas, _orden as _orden_nombre
from tele import ErrorTele, Teles

# ------------------------------------------------------------------ ajustes
# Valores por defecto. Se cambian en rockola.toml (ver rockola.ejemplo.toml), no acá.
PORT = 8000
HTTPS_PORT = 8443      # el micrófono necesita https: un segundo puerto con certificado propio (0: sin él)
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
VOTOS_SALTEAR = 3         # cuántos tienen que votar ⏭ para que la que suena se saltee sola (0 = no se vota)
CROSSFADE = 6             # segundos que se pisan dos canciones con el crossfade prendido (se prende
                          # desde la web; 0 = no hay crossfade)
CLIMA = ""                # el clima de la barra de arriba: "" = el de donde está la rockola (según su
                          # IP), una ciudad ("Rosario") o False para no mostrarlo
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
ID_ANUNCIO = re.compile(r"[0-9a-f]{10}")
RANGO = re.compile(r"bytes=(\d*)-(\d*)")   # el pedazo de un archivo que pide el navegador
FUENTE = re.compile(r"/fuentes/([a-z0-9-]+\.woff2)")   # la letra de las páginas (static/fuentes)
UN_ANIO = 365 * 24 * 3600
TIPOS_AUDIO = {".webm": "audio/webm", ".weba": "audio/webm", ".opus": "audio/ogg", ".ogg": "audio/ogg",
               ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".mp3": "audio/mpeg", ".aac": "audio/aac"}

# -------------------------------------------------------------------- estado
lock = threading.Lock()
changed = threading.Condition(lock)   # despierta a los que esperan cambios
state = {
    "version": 0,      # sube con cada cambio, en cualquier sala (el Director lo mira)
    "mode": "navegador",
    "volume": None,    # el del parlante (sala principal)
    "crossfade": False,  # si las canciones se pisan al cambiar (se prende desde la web; ver Director.fundir)
    "saving": [],      # las que pidieron guardar sin ponerlas en la cola (modo mpv), de todas las salas
    "sin_red": False,  # no hay internet: las de "saving" esperan a que vuelva (ver bucle_guardar)
    "online": None,    # si hay internet (ver bucle_internet): el led de las páginas y de la pantalla. None: todavía no se sabe
    "cpu_temp": None,  # la temperatura del procesador en °C (ver bucle_temperatura), o None si esta máquina no la da
    "weather": None,   # el clima (ver bucle_clima): { temp, text, icon, symbol, city }, o None si no se sabe
}
REACCIONES = ("🔥", "❤️", "😴")   # con las que se reacciona a la que suena
MAX_GUARDANDO = 100     # cuántas pueden estar esperando para bajarse así, entre todos (un álbum son ~12)
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
        self.previstas = []                # con el aleatorio prendido: las (hasta 3) que van a sonar si nadie pide (ver rellenar_previstas)
        self.history = []                  # ya sonaron
        self.progress = {"t": 0, "d": 0, "playing": False}
        self.loading = False               # bajando la que tiene que sonar
        self.notice = None                 # {"id", "text"}: último aviso (p. ej. una canción salteada)
        self.shuffle = False               # con la cola vacía, suenan guardadas al azar (modo mpv)
        self.owners = {}                   # item id -> client id (quién la pidió, para poder quitarla)
        self.votos = {}                    # item id -> [client ids]: los 👍 de cada una de la cola
        self.fijas = set()                 # ids de las que puso en su lugar alguien con el PIN (⤒, o arrastrándola):
                                           # una con más votos no les pasa por arriba
        self.saltear = []                  # client ids que votaron saltear la que suena
        self.reacciones = {}               # emoji -> [client ids]: las reacciones a la que suena
        self.reaccion = None               # {"n", "emoji", "by"}: la última (las pantallas la muestran un instante)
        self.anotadas = set()              # (client, emoji): las reacciones a la que suena que ya se anotaron en el chat
        self.chat = []                     # [{"id", "by", "text", "at", "client"}]; los del registro, además, "log"
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
voz = None             # Voz: los anuncios y el micrófono (ver voz.py)
tapas = None           # Tapas: las copias de las tapas de las canciones guardadas (ver tapas.py)
letras_guardadas = None   # Letras: las copias de las letras de las canciones guardadas (ver letras.py)
videos = {}            # videoId -> datos de un video (de lo que se buscó para una sala de tv)
fallos_pin = {}        # ip -> (intentos fallidos, bloqueado hasta)



def cargar_config(ruta):
    """Lee rockola.toml (si existe) y pisa los valores por defecto."""
    global HTTPS_PORT, PORT, MAX_PER_PERSON, MAX_DURATION, VOLUME, NORMALIZAR, MPV_ARGS, PIN, IP_FIJA, MODO
    global GUARDAR_GB, CARPETA, PREDESCARGAR, AZAR_AL_PRENDER, CLIMA, CROSSFADE, VOTOS_SALTEAR
    if not ruta.exists():
        return
    with open(ruta, "rb") as f:
        c = tomllib.load(f)
    PORT = int(c.get("puerto", PORT))
    HTTPS_PORT = int(c.get("https_puerto", HTTPS_PORT))
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
    CROSSFADE = max(0.0, min(15.0, float(c.get("crossfade", CROSSFADE))))
    VOTOS_SALTEAR = max(0, int(c.get("votos_para_saltear", VOTOS_SALTEAR)))
    clima = c.get("clima", CLIMA)
    CLIMA = False if clima is False else str(clima).strip()


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
        if voz:
            threading.Thread(target=voz.borrar_sala, args=(sala.id,), daemon=True).start()
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


def _bump_sala(sid):
    with lock:
        if sid in salas:
            bump(salas[sid])


def voz_estado(sala):
    if not voz:
        raise ApiError(409, "La voz sólo anda en modo mpv")
    return {"voz": voz.estado(sala.id)}


def voz_accion(sala, path, body):
    """Lo que se le pide a la voz de una sala con un JSON (con el PIN de la sala): el micrófono, y cambiar un anuncio."""
    if not voz:
        raise ApiError(409, "La voz sólo anda en modo mpv")
    aid = str(body.get("id", ""))
    if path == "/api/voz-estado":   # (es POST: los GET de la página no llevan el PIN)
        pass
    elif path == "/api/voz-mic":
        voz.mic_abrir(sala.id) if body.get("on") else voz.mic_cerrar(sala.id)
    elif path == "/api/voz-anuncio-editar":
        voz.anuncio_editar(sala.id, aid, body)
    elif path == "/api/voz-anuncio-borrar":
        voz.anuncio_borrar(sala.id, aid)
    elif path == "/api/voz-anuncio-sonar":
        voz.anuncio_sonar(sala.id, aid)
    else:
        raise ApiError(404, "No existe")


def copiar_tapas(limpiar=False):
    """Que haya una copia de la tapa de cada canción guardada (ver tapas.py): las que faltan se
    van bajando. `limpiar`: además, borrar las de canciones que ya no están guardadas."""
    if not (tapas and director):
        return
    urls = director.descargas.tapas()
    if limpiar:
        tapas.quedarse_con(urls)
    tapas.guardar(urls)


def en_uso():
    """Las canciones que no se pueden borrar: en cada sala, la que suena, las que vienen (las que
    se bajan por adelantado) y la anterior (para que ⏮ sea instantáneo)."""
    with lock:
        usa = set()
        for s in salas.values():
            usa |= {i["videoId"] for i in [s.now, *s.queue[:PREDESCARGAR or None], *s.previstas, *s.history[-1:]] if i}
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
        _al_chat(sala, {"by": name, "text": texto, "client": client})
        bump(sala)


def _al_chat(sala, entrada):
    """Un renglón más en el chat de la sala. Con el lock tomado; avisa (bump) el que llama."""
    n = sala.chat[-1]["id"] + 1 if sala.chat else 1
    sala.chat = (sala.chat + [dict(entrada, id=n, at=time.time())])[-MAX_CHAT:]


def anotar(sala, icono, quien, texto, client=""):
    """El registro: un renglón en el chat que dice quién hizo qué con la música (votó saltearla,
    reaccionó, la salteó, puso otra). `quien`: el nombre que puso en la página ("" si no hay: lo
    hicieron desde la rockola, y `texto` lo dice entero). Se ve como «12:03 ⏭ Juli salteó “Tema”».
    Con el lock tomado; avisa (bump) el que llama."""
    _al_chat(sala, {"by": quien, "text": texto, "client": client, "log": icono})


def _nombre(body, sin="Alguien"):
    """El nombre que viene con un pedido (el que puso en la página), o `sin` si no puso."""
    return str(body.get("by") or "").strip()[:30] or sin


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
# (por si se corta la luz) y al apagar la rockola. El chat no: se borra. Ahí quedan también las
# que esperan para bajarse (⬇), en su orden: al volver a arrancar se siguen bajando.

CLAVES_ITEM = ("id", "videoId", "title", "artist", "duration", "thumb", "album", "by", "playlist", "azar",
               "reacciones")
CLAVES_A_BAJAR = ("videoId", "title", "artist", "datos", "album", "salas", "clientes")   # de state["saving"]
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
            "votes": {k: v for k, v in s.votos.items() if k in en_cola and v},
            "pinned": [k for k in s.fijas if k in en_cola],
        }
    # Las que esperan para bajarse (también la que se está bajando: si se corta, se baja de nuevo).
    # Las que fallaron no: son un aviso que dura un minuto.
    a_bajar = [{k: g[k] for k in CLAVES_A_BAJAR} for g in state["saving"] if not g["error"]]
    return {"salas": fotos, "saving": a_bajar}


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
    if "reacciones" in item:   # (las que tuvo una que ya sonó: {"🔥": 3})
        r = item["reacciones"] if isinstance(item["reacciones"], dict) else {}
        item["reacciones"] = {e: int(r[e]) for e in REACCIONES if isinstance(r.get(e), int) and r[e] > 0}
        if not item["reacciones"]:
            del item["reacciones"]
    return item


def _a_bajar_guardada(g):
    """Una de las que esperaban para bajarse, leída de colas.json, como va en state["saving"]; o
    None si no está bien o si ya no existe ninguna de las salas que la habían pedido."""
    if not isinstance(g, dict) or not VIDEO_ID.match(str(g.get("videoId", ""))):
        return None
    lista = lambda k: [x for x in g[k] if isinstance(x, str)] if isinstance(g.get(k), list) else []
    en = [sid for sid in lista("salas") if sid in salas]
    datos = g.get("datos") if isinstance(g.get("datos"), dict) else {}
    try:
        duracion = int(datos.get("duration") or 0)
    except (TypeError, ValueError):
        duracion = 0
    if not en or duracion <= 0:
        return None
    texto = lambda v: str(v or "")[:300]
    titulo, artista, album = texto(g.get("title") or datos.get("title")), texto(g.get("artist") or datos.get("artist")), g.get("album")
    return {"videoId": g["videoId"], "title": titulo, "artist": artista,
            "datos": {"title": titulo, "artist": artista, "duration": duracion, "thumb": texto(datos.get("thumb")),
                      "album": texto(datos.get("album"))},
            "error": None, "hasta": 0, "album": album if isinstance(album, str) and ALBUM_ID.match(album) else None,
            "salas": en, "clientes": [c[:64] for c in lista("clientes")], "bajando": False}


def retomar_colas(en_pausa=False):
    """Al arrancar: cada sala como estaba la última vez (lo que sonaba y en qué segundo, la cola,
    lo que sonó recién), y las que esperaban para bajarse (⬇), en su orden. Con `en_pausa`, lo de
    la principal queda en pausa (ver main: a las 6 de la mañana la oficina está vacía). Devuelve
    True si la principal tiene una que seguía sonando (que no estaba en pausa)."""
    ruta = carpeta() / "colas.json"
    try:
        doc = json.loads(ruta.read_text())
        guardadas = doc["salas"]
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
            votos = d.get("votes") if isinstance(d.get("votes"), dict) else {}
            s.votos = {k: [str(c)[:64] for c in v if isinstance(c, str)] for k, v in votos.items()
                       if k in en_cola and isinstance(v, list)}
            s.fijas = {k for k in (d.get("pinned") if isinstance(d.get("pinned"), list) else []) if k in en_cola}
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
        # Las que esperaban para bajarse: las sigue bajando el Director (ver bucle_guardar). Si
        # alguna ya se había bajado (se apagó justo), ahí mismo queda guardada, sin bajarla.
        a_bajar = doc.get("saving") if isinstance(doc.get("saving"), list) else []
        if state["mode"] == "mpv" and GUARDAR_GB > 0:
            for g in filter(None, map(_a_bajar_guardada, a_bajar)):
                if len(state["saving"]) < MAX_GUARDANDO and not any(x["videoId"] == g["videoId"] for x in state["saving"]):
                    state["saving"].append(g)
            if state["saving"]:
                bump()
    if state["saving"]:
        n = len(state["saving"])
        print(f"  ⬇ Sigo con las descargas: {n} {'canción esperaba' if n == 1 else 'canciones esperaban'} para bajarse", flush=True)
    if cuantas:
        print(f"  ⏯ Retomo donde estaba: {cuantas} {'canción' if cuantas == 1 else 'canciones'} entre lo que "
              "sonaba y las colas", flush=True)
    return seguia


# ------------------------------------------------------------- YouTube Music

class _PozoYT:
    """Unas pocas sesiones de YTMusic que se reutilizan. requests.Session no es thread-safe, así que cada
    llamada toma una para ella sola y la devuelve al terminar. (Antes había una por hilo, pero el servidor
    hace un hilo por pedido: cada búsqueda armaba una sesión nueva y un handshake TLS nuevo con YouTube.)"""
    MAX = 4

    def __init__(self):
        self.libres = []
        self.lock = threading.Lock()

    def __getattr__(self, nombre):
        def llamar(*a, **kw):
            with self.lock:
                api = self.libres.pop() if self.libres else None   # (la última que se usó: la más "caliente")
            if api is None:
                api = YTMusic()
            try:
                return getattr(api, nombre)(*a, **kw)
            finally:
                with self.lock:
                    if len(self.libres) < self.MAX:
                        self.libres.append(api)
        return llamar


_pozo_yt = _PozoYT()


def yt():
    return _pozo_yt


_busquedas = {}              # (qué, texto) -> (cuándo, resultado): las últimas búsquedas, un rato
_busquedas_lock = threading.Lock()
BUSQUEDA_VALE, BUSQUEDAS_MAX = 120, 64   # segundos que vale una búsqueda repetida, y cuántas se recuerdan


def _recordada(que, q, buscar):
    """`buscar(q)`, pero si hace poco se buscó lo mismo (borrar una letra, volver a escribirla) se
    contesta al toque, sin ir a YouTube. Devuelve copias: quien llama puede tocarlas."""
    clave, ahora = (que, " ".join(q.casefold().split())), time.monotonic()
    with _busquedas_lock:
        hay = _busquedas.get(clave)
    if hay and ahora - hay[0] < BUSQUEDA_VALE:
        return [dict(x) for x in hay[1]]
    res = buscar(q)
    with _busquedas_lock:
        if len(_busquedas) >= BUSQUEDAS_MAX:
            for k in sorted(_busquedas, key=lambda k: _busquedas[k][0])[:BUSQUEDAS_MAX // 4]:
                del _busquedas[k]
        _busquedas[clave] = (ahora, res)
    return [dict(x) for x in res]


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


letras = {}            # videoId -> su letra (también las que no tienen: no volver a preguntar). En memoria, las
                       # últimas; las de las guardadas, además, quedan copiadas en el disco (letras_guardadas)
letras_en_camino = {}  # videoId -> Event: si la piden varios a la vez, se busca una sola vez
lock_letras = threading.Lock()


def letra(video_id):
    """La letra de una canción según YouTube Music, con el tiempo de cada renglón si lo tiene:

        {"synced": True, "source": "LyricFind", "lines": [{"t": 9.2, "f": 10.63, "text": "…"}, …]}

    (`t` y `f`: cuándo empieza y termina, en segundos). Sin tiempos, `synced` es False y los
    renglones sólo traen `text`; sin letra, `lines` viene vacío. Si YouTube no responde, tira
    la excepción y no queda guardado: se vuelve a probar la próxima vez.

    La de una canción guardada sale de la copia que tiene la rockola (ver _letra_con_copia): no
    se le pregunta a nadie, y está aunque se corte internet.
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
        datos, guardar = _letra_con_copia(video_id)
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


def _letra_con_copia(video_id):
    """(letra, si se puede recordar). Si la rockola tiene su copia (ver letras.py), es esa, sin
    preguntarle a nadie. Si no la tiene, se busca y, si la canción está guardada, queda copiada.
    Una copia vencida (no tenía letra, o no tenía tiempos, y pasó un mes) se vuelve a preguntar:
    si ahora hay algo mejor, se cambia; si no, o si no se puede preguntar, sigue valiendo."""
    c = letras_guardadas
    vieja, vencida = c.leer(video_id) if c else (None, False)
    if vieja is not None and not vencida:
        return vieja, True
    if vieja is not None and state["online"] is False:
        return vieja, False   # sin internet no hay a quién preguntarle: la que hay (y se prueba la próxima vez)
    try:
        datos, guardar = _buscar_letra(video_id)
    except Exception:
        if vieja is None:
            raise
        return vieja, False
    if vieja is not None:
        mejor = guardar and (datos["synced"] or (bool(datos["lines"]) and not vieja["lines"]))
        if not mejor:
            if guardar:
                c.guardar(video_id, vieja)   # se preguntó y no hay nada mejor: vale otro mes
            return vieja, guardar
    if guardar and c and director and director.descargas.datos(video_id) is not None:   # (sólo las de las guardadas)
        c.guardar(video_id, datos)
    return datos, guardar


def _copiar_letra(video_id):
    """Para letras.Letras (ahí dice qué contesta): que quede copiada la letra de una guardada
    que todavía no la tiene."""
    c = letras_guardadas
    if not c or not director or director.descargas.datos(video_id) is None:
        return "ya"   # (dejó de estar guardada: nada que copiar)
    with lock_letras:
        sabida = video_id in letras   # ya la pidió una página (p. ej. recién, cuando empezó a sonar): no hay que preguntar
    try:
        datos = letra(video_id)
    except Exception:
        return "red"   # YouTube no contestó (se habrá cortado internet)
    if c.leer(video_id)[0] is None:
        with lock_letras:
            firme = video_id in letras
        if not firme:
            return "no"   # una respuesta que no se da por buena (YouTube o LRCLIB fallaron un rato)
        c.guardar(video_id, datos)
    return "ya" if sabida else "ok"


def copiar_letras(limpiar=False):
    """Que haya una copia de la letra de cada canción guardada (ver letras.py): las que faltan
    se van buscando, de a una. `limpiar`: además, borrar las de canciones que ya no están guardadas."""
    if letras_guardadas and director:
        letras_guardadas.completar(director.descargas.ids(), limpiar)


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
    return _recordada("temas", q, _search)


def _search(q):
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
    return _recordada("albumes", q, _buscar_albumes)


def _buscar_albumes(q):
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


DE_AUDIO = "MUSIC_VIDEO_TYPE_ATV"   # en YouTube Music: un tema de audio (lo demás son videos)


def _temas_de_audio(a):
    """Los temas de un álbum (lo que devuelve ytmusicapi), en su versión de audio. YouTube Music
    a veces pone, en lugar del tema del disco, su video oficial: es otra grabación (con la
    intro del video) y puede tener restricción de edad, y entonces no se baja. El álbum trae
    aparte su lista «de audio»: de ahí sale el tema del disco. Los que ya están guardados con el
    id del video quedan así (si no, ese tema aparecería dos veces entre las guardadas)."""
    from reproductor import _para_buscar

    temas = a.get("tracks") or []
    cambia = lambda t: (t.get("videoId") and t.get("videoType") not in (None, DE_AUDIO)
                        and not (director and director.descargas.datos(t["videoId"])))
    if not a.get("audioPlaylistId") or not any(cambia(t) for t in temas):
        return temas
    try:
        de_audio = [x for x in yt().get_playlist(a["audioPlaylistId"], limit=len(temas) + 20).get("tracks") or []
                    if VIDEO_ID.match(x.get("videoId") or "") and x.get("videoType") == DE_AUDIO]
    except Exception:   # sin la lista de audio quedan como vinieron
        return temas
    clave = lambda t: _para_buscar(t.get("title")).strip()
    por_titulo = {}
    for x in de_audio:
        por_titulo.setdefault(clave(x), x["videoId"])
    return [dict(t, videoId=por_titulo.get(clave(t), t["videoId"])) if cambia(t) else t for t in temas]


def version_de_audio(video_id, datos):
    """El id de otro video de YouTube con el mismo tema, en versión de audio: para cuando el que
    se pidió es un video con restricción de edad, que sin una cuenta no se baja (lo llama
    Descargas). Primero en su álbum (ahí está seguro, si el álbum es ése); si no, entre los temas
    que YouTube Music encuentra con ese título y ese artista. None si no aparece."""
    from reproductor import _para_buscar, _primer_artista

    datos = datos if datos.get("title") else meta_cache.get(video_id) or {}
    titulo, artista = str(datos.get("title") or ""), str(datos.get("artist") or "")
    clave, primero = _para_buscar(titulo).strip(), _para_buscar(_primer_artista(artista)).strip()
    if not clave:
        return None
    if datos.get("album"):
        try:
            for c in get_album(album_llamado(datos["album"], artista))["songs"]:
                if c["videoId"] != video_id and _para_buscar(c["title"]).strip() == clave:
                    return c["videoId"]
        except Exception:   # no está el álbum, o YouTube no contesta: se prueba buscando el tema
            pass
    # (el título primero: con el artista adelante YouTube Music a veces no trae el tema del disco)
    for buscar in (f"{titulo} {_primer_artista(artista)}", f"{_primer_artista(artista)} {titulo}"):
        iguales = []
        for r in yt().search(buscar, filter="songs", limit=20):
            suyos = _para_buscar(", ".join(a["name"] for a in r.get("artists") or [] if a.get("name")))
            if (r.get("videoType") == DE_AUDIO and VIDEO_ID.match(r.get("videoId") or "") and r["videoId"] != video_id
                    and _para_buscar(r.get("title")).strip() == clave and primero and primero in suyos):
                iguales.append((abs((r.get("duration_seconds") or 0) - (datos.get("duration") or 0)), r["videoId"]))
        if iguales:
            return min(iguales)[1]   # (si hay varias, la que dura más parecido)
    return None


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
        canciones, no_disponibles = _canciones(_temas_de_audio(a), tapa)
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
    return {"id": pl["id"], "title": pl["title"], "author": pl["author"], "thumb": pl["thumb"],
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
        return stats.generos_de(director.descargas.autores(sid))
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


def artista_guardado(sid, clave):
    """Lo guardado en la sala de un artista (`clave`: su "key" en la lista de artistas): sus temas
    y los álbumes en los que está."""
    if not director:
        raise ApiError(409, "Las canciones guardadas sólo existen en modo mpv")
    a = director.descargas.del_artista(clave, sala=sid)
    if not a:
        raise ApiError(404, "No hay guardadas de ese artista")
    return a


def _mezcladas(titulo, kind, temas):
    """Una colección que no tiene un orden (todo lo guardado de un género o de un artista): va
    mezclada y, si son muchas, las que entran (cada vez, otras)."""
    temas = list(temas)
    random.shuffle(temas)
    maximo = f"del máximo de {MAX_PLAYLIST} por vez"
    return {"title": titulo, "kind": kind, "songs": temas[:MAX_PLAYLIST],
            "afuera": [(len(temas) - MAX_PLAYLIST, f"pasa {maximo}", f"pasan {maximo}")]}


def coleccion(sala, body):
    """Una colección de temas entera, para cargarla a la cola o sumarla a una playlist, según
    `from`: una playlist de la rockola (`list`, su id), un álbum de YouTube Music (`browseId`), uno de las
    guardadas (`album`, `artist` y sus `videoIds`), una playlist de YouTube (`q`, el link) o lo
    guardado de un género (`genre`, su clave) o de un artista (`artist`, la suya).
    Devuelve {title, kind, songs, afuera}: `kind` es "album", "lista", "youtube", "genero" o
    "artista"; `afuera`, los que no vinieron, como (cuántos, uno, varios)."""
    de = body.get("from")
    if de == "genre":
        g = genero_guardado(sala.id, str(body.get("genre", ""))[:80])
        return _mezcladas(g["genre"] or "Sin género", "genero", g["songs"])
    if de == "artist":
        a = artista_guardado(sala.id, str(body.get("artist", ""))[:200])
        return _mezcladas(a["artist"] or "Sin artista", "artista", a["songs"])
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
    name = str(body.get("by", "")).strip()[:30] or {"album": "Álbum", "genero": "Género",
                                                      "artista": "Artista"}.get(col["kind"], "Playlist")
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
            # Lo pedido y, con el aleatorio, las elegidas al azar hasta completar PREVISTAS (con `azar`: no se votan ni se tocan)
            "queue": [dict(i, mine=bool(client) and sala.owners.get(i["id"]) == client,
                           votes=len(sala.votos.get(i["id"], ())),
                           voted=bool(client) and client in sala.votos.get(i["id"], ()))
                      for i in sala.queue] + [dict(p, mine=False, votes=0, voted=False) for p in previstas_visibles(sala)],
            # Saltear por votos: cuántos van, cuántos hacen falta y si este ya votó (None: no se vota).
            "skip": {"votes": len(sala.saltear), "needed": VOTOS_SALTEAR,
                     "voted": bool(client) and client in sala.saltear} if VOTOS_SALTEAR and sala.now else None,
            # Las reacciones a la que suena: cuántas de cada una, cuáles puso este y la última de todas.
            "reactions": {"counts": {e: len(sala.reacciones.get(e, ())) for e in REACCIONES},
                          "mine": [e for e in REACCIONES if client and client in sala.reacciones.get(e, ())],
                          "last": sala.reaccion},
            "history": sala.history[-10:][::-1],
            "progress": progreso_ahora(sala),
            # (sin parlante: hasta que la que suena esté bajada y arranque, "cargando")
            "loading": sala.loading or (not sala.principal and sala.now is not None and not sala.lista),
            "volume": state["volume"] if sala.principal else None,
            # (None: acá no hay crossfade; sólo en la sala del parlante, en modo mpv)
            "crossfade": state["crossfade"] if director and sala.principal and CROSSFADE else None,
            "notice": sala.notice,
            "shuffle": sala.shuffle,
            # Sin parlante: la pausaron (con el PIN), o está en espera porque nadie la escucha.
            "paused": None if sala.principal else sala.pausada,
            "waiting": not sala.principal and sala.now is not None and sala.lista and not sala.pausada
                       and not sala.progress["playing"],
            "listeners": sum(ahora - t < OYENTE_VENCE for t in sala.oyentes.values()),
            # (los del registro llevan "log": su ícono; ver anotar)
            "chat": [{"id": m["id"], "by": m["by"], "text": m["text"], "at": m["at"],
                      "mine": bool(client) and m["client"] == client, **({"log": m["log"]} if m.get("log") else {})}
                     for m in sala.chat[-50:]],
            "library": director.descargas.resumen(sala.id) if director else None,
            "lists": {"v": listas.version(sala.id)} if listas else None,   # (las pide aparte: /api/lists)
            "saving": [dict({k: g.get(k) for k in ("videoId", "title", "artist", "error", "album")},
                            thumb=g["datos"].get("thumb"), duration=g["datos"].get("duration"),
                            active=g["bajando"], mine=bool(client) and client in g["clientes"])
                       for g in state["saving"] if sala.id in g["salas"]],
            # (la fila para bajar es una sola: cuántas de otras salas hay esperando en ella)
            "savingOthers": sum(not g["error"] and sala.id not in g["salas"] for g in state["saving"]),
            "savingOffline": state["sin_red"],   # no hay conexión: esperan a que vuelva
            "online": state["online"],           # si la rockola tiene internet (el led)
            # Un anuncio o el micrófono en el aire (sólo en la sala del parlante): {"mic", "anuncio"}, o None
            "voice": (lambda v: v if v["mic"] or v["anuncio"] else None)(voz.en_el_aire(sala.id)) if voz else None,
            "weather": state["weather"],         # el clima, para la barra de arriba
            "cpu_temp": state["cpu_temp"],       # la temperatura del procesador, junto al clima
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


COMPRIMIR_DESDE = 1024   # bytes: lo que pesa menos va como está
_paginas = {}            # nombre -> (cuándo se cambió el archivo, la página, la página comprimida)
_paginas_lock = threading.Lock()


def pagina(name):
    """Una página de static/, y lo mismo comprimido. Se comprime una vez (y de nuevo si cambia el
    archivo: así una versión nueva se ve sin reiniciar): index.html pasa de unos 180 KB a 50."""
    ruta = STATIC / name
    cuando = ruta.stat().st_mtime_ns
    with _paginas_lock:
        hay = _paginas.get(name)
        if hay and hay[0] == cuando:
            return hay[1], hay[2]
    data = ruta.read_bytes()
    gz = gzip.compress(data, compresslevel=9)
    with _paginas_lock:
        _paginas[name] = (cuando, data, gz)
    return data, gz


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


# A quién se le pregunta si hay internet. Primero a Google y a Cloudflare por su IP: sin DNS de
# por medio, contestan (o fallan) enseguida. Si ninguno contesta, a Google por nombre, por si en
# esa red esas IP están bloqueadas.
INTERNET = (("8.8.8.8", 443), ("1.1.1.1", 443), ("www.google.com", 443))


def hay_internet(timeout=3):
    """¿Hay internet? Prueba conectarse a alguno de INTERNET: como un ping, pero con una conexión
    TCP (el ping de verdad pide permisos). Alcanza con que conteste uno."""
    for destino in INTERNET:
        try:
            socket.create_connection(destino, timeout=timeout).close()
            return True
        except OSError:
            pass
    return False


def bucle_internet(cada=15, cortado=5):
    """Mira cada `cada` segundos si hay internet (mientras no hay, cada `cortado`: para enterarse
    enseguida cuando vuelve) y lo deja en state["online"]. Es el led de las páginas y de la
    pantalla: sin internet la música sigue con lo guardado, pero no se puede buscar ni bajar."""
    while True:
        hay = hay_internet()
        with lock:
            antes = state["online"]
            if hay != antes:
                state["online"] = hay
                bump()
        if hay != antes:
            if teles:
                teles.avisar()   # (las salas de tv esperan en otro lado: que también se enteren)
            if not hay:
                print("  ⚠ sin internet: sigue sonando lo guardado", flush=True)
            elif antes is False:
                print("  ✓ volvió internet", flush=True)
        time.sleep(cada if hay else cortado)


# ------------------------------------------------------------------ el clima
# Para la barra de arriba de las páginas y de la pantalla. Lo da Open-Meteo (open-meteo.com: sin
# cuenta ni clave). Se le manda dónde: la ciudad de `clima` en rockola.toml o, sin nada, la que
# sale de la IP pública de la rockola (se le pregunta a ipwho.is o, si no contesta, a GeoJS).

# Los códigos del tiempo son los de la OMM (los que da Open-Meteo), de menor a mayor: hasta cuál,
# cómo se dice, el emoji (para las páginas) y un símbolo de un solo color y una celda de ancho
# (para la terminal, donde los emojis con variantes corren lo que sigue).
CLIMAS = (
    (0, "despejado", "☀️", "☀"), (1, "casi despejado", "🌤️", "☀"), (2, "algo nublado", "⛅", "☁"),
    (3, "nublado", "☁️", "☁"), (48, "niebla", "🌫️", "≡"), (57, "llovizna", "🌦️", "☂"),
    (67, "lluvia", "🌧️", "☂"), (77, "nieve", "🌨️", "❄"), (82, "chaparrones", "🌧️", "☂"),
    (86, "nevadas", "🌨️", "❄"), (99, "tormenta", "⛈️", "☈"),
)
UA = {"User-Agent": "Rockola/2.0 (rockola de oficina)"}


def _json_de(url, timeout=10):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read())


def ubicar_clima():
    """De dónde es el clima: (latitud, longitud, nombre del lugar). Con `clima = "Rosario"` en
    rockola.toml, esa ciudad (o "-32.95, -60.64": el punto justo); sin nada, donde está la rockola
    según su IP pública (da la ciudad, más o menos: si le erra, poné la tuya)."""
    if CLIMA:
        punto = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)", CLIMA)
        if punto:
            return float(punto[1]), float(punto[2]), ""
        hay = _json_de("https://geocoding-api.open-meteo.com/v1/search?"
                       + urlencode({"name": CLIMA, "count": 1, "language": "es"})).get("results")
        if not hay:
            raise ValueError(f"no encontré la ciudad «{CLIMA}» (probá con otro nombre, o con «latitud, longitud»)")
        return float(hay[0]["latitude"]), float(hay[0]["longitude"]), str(hay[0].get("name") or CLIMA)
    error = None
    for url in ("https://ipwho.is/", "https://get.geojs.io/v1/ip/geo.json"):
        try:
            d = _json_de(url)
            return float(d["latitude"]), float(d["longitude"]), str(d.get("city") or d.get("region") or "")
        except Exception as e:   # no contestó, o contestó otra cosa: el otro
            error = e
    raise error


def leer_clima(lat, lon):
    """El tiempo de ahora en ese lugar: la temperatura (°C) y cómo está (texto, emoji y símbolo)."""
    q = urlencode({"latitude": round(lat, 2), "longitude": round(lon, 2), "current": "temperature_2m,weather_code,is_day"})
    ahora = _json_de(f"https://api.open-meteo.com/v1/forecast?{q}")["current"]
    codigo = int(ahora["weather_code"])
    texto, emoji, simbolo = next((c[1:] for c in CLIMAS if codigo <= c[0]), CLIMAS[-1][1:])
    if not ahora.get("is_day") and codigo <= 1:   # de noche, despejado: la luna
        emoji, simbolo = "🌙", "☾"
    return {"temp": round(float(ahora["temperature_2m"])), "text": texto, "icon": emoji, "symbol": simbolo}


def bucle_clima(cada=600, reintento=60, vence=2 * 3600):
    """Mira el clima cada `cada` segundos y lo deja en state["weather"]: va en la barra de arriba
    de las páginas y de la pantalla. Si no se puede (no hay internet, o no contestan), queda el
    último hasta que tenga `vence` segundos; después, nada."""
    donde, leido, avisado = None, 0, False
    while True:
        nuevo = None   # (None: esta vez no se pudo)
        try:
            if donde is None:
                donde = ubicar_clima()
                print(f"  ⛅ El clima: el de {donde[2] or 'ese punto'}"
                      + ('' if CLIMA else ' (por la IP; se cambia con clima = "…" en rockola.toml)'), flush=True)
            nuevo = dict(leer_clima(*donde[:2]), city=donde[2])
            leido, avisado = time.time(), False
        except Exception as e:   # sin internet, un servicio caído, una respuesta rara: se prueba de nuevo
            if not avisado and state["online"] is not False:
                print(f"  ⚠ no pude ver el clima: {e}", flush=True)
            avisado = True
        espera = cada if nuevo else reintento
        with lock:
            if nuevo is None and time.time() - leido < vence:
                nuevo = state["weather"]
            if nuevo != state["weather"]:
                state["weather"] = nuevo
                bump()
        time.sleep(espera)


# ------------------------------------------------------------ temperatura del procesador
# Junto al led y al clima. Se lee del sistema (en la Raspberry, /sys/class/thermal): sin ese archivo
# (otra máquina) no se muestra nada. Pasados los 80 °C la Pi empieza a bajar la velocidad.
ARCHIVO_TEMP = Path("/sys/class/thermal/thermal_zone0/temp")


def leer_temperatura():
    """Los °C del procesador, redondeados, o None si no se pueden leer."""
    try:
        return round(int(ARCHIVO_TEMP.read_text()) / 1000)
    except (OSError, ValueError):
        return None


def bucle_temperatura(cada=10):
    """Mira la temperatura cada `cada` segundos y la deja en state["cpu_temp"] (avisa sólo cuando cambia
    el grado). Si esta máquina no la da, termina."""
    if leer_temperatura() is None:
        return
    while True:
        t = leer_temperatura()
        with lock:
            if t != state["cpu_temp"]:
                state["cpu_temp"] = t
                bump()
        time.sleep(cada)


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


def save_playlist(sala, body):
    """⬇ de una playlist de YouTube: baja sus temas (como los de un álbum) y arma con ellos una
    ♫ playlist de la rockola con el mismo nombre. Si ya hay una con ese nombre (p. ej. porque se
    bajó antes), se le suman los que falten. Cualquiera puede, como con un álbum."""
    _se_guarda()
    client = str(body.get("client", ""))[:64]
    pid = _id_playlist(str(body.get("q", ""))[:300])
    pl = get_playlist(pid)
    try:
        r = _guardar_coleccion(sala, client, pid, dict(pl, songs=[c for c in pl["songs"] if _se_puede(c)]))
    except ApiError as e:
        if e.code != 409:
            raise
        # Nada nuevo para bajar (ya se están bajando, p. ej.): igual queda la ♫ playlist.
        r = {"browseId": pid, "added": 0, "complete": False, "text": e.msg}
    r["text"] += _lista_de_playlist(sala, client, pl)
    return r


def _lista_de_playlist(sala, client, pl):
    """Arma (o completa) la ♫ playlist de la rockola con los temas de `pl`. Devuelve el
    texto que se suma al aviso; nunca falla: lo importante es la descarga."""
    if not listas:
        return ""
    temas = [c for c in pl["songs"] if _se_puede(c)]
    if not temas:
        return ""
    try:
        try:
            lista, nueva = listas.crear(sala.id, pl["title"], "", client), True
        except ErrorLista as e:
            if e.code != 409:
                raise
            existente = next((l for l in listas.de_sala(sala.id, client)["lists"] if _orden_lista(l["name"]) == _orden_lista(pl["title"])), None)
            if not existente:
                raise
            lista, nueva = existente, False
        n, *_ = listas.sumar(sala.id, lista["id"], temas, client, False)
    except ErrorLista as e:
        return f" (No pude armar la ♫ playlist: {e.msg})"
    _cambio_en_listas(sala)
    if nueva:
        return f" Armé la ♫ playlist «{lista['name']}» con {_contar(n, 'tema', 'temas')}."
    return f" Sumé {_contar(n, 'tema', 'temas')} a la ♫ playlist «{lista['name']}», que ya existía." if n else \
           f" La ♫ playlist «{lista['name']}» ya tenía todos."


def _orden_lista(s):
    return _orden_nombre(s)


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
    return _guardar_coleccion(sala, client, bid, get_album(bid))


def _guardar_coleccion(sala, client, clave, al):
    """Lo que tienen en común ⬇ un álbum y ⬇ una playlist de YouTube: baja los temas de `al`
    ({"title", "songs", "unavailable"}) que faltan. `clave`: con qué se los agrupa en la fila de
    descargas (el browseId del álbum, o el id de la playlist)."""
    bid = clave
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


def mover_guardado(sala, body):
    """⤒ ↑ ↓ en ⬇ descargas (con el PIN): cambia el orden en que se bajan las que esperan. `to`
    es el lugar entre las de la sala que esperan (0 = la próxima). La que ya se está bajando no
    se mueve, ni se le pasa adelante. La fila es una sola para todas las salas: la canción queda
    justo antes de la que estaba en ese lugar (o después de la última de la sala)."""
    vid = str(body.get("videoId", ""))
    try:
        to = int(body.get("to", 0))
    except (TypeError, ValueError):
        raise ApiError(400, "Posición inválida")
    with lock:
        fila = state["saving"]
        de_la_sala = [g for g in fila if sala.id in g["salas"] and not g["error"]]
        g = next((x for x in de_la_sala if x["videoId"] == vid), None)
        if g is None:
            raise ApiError(404, "Esa canción ya no está esperando para bajarse")
        if g["bajando"]:
            raise ApiError(409, "Esa ya se está bajando")
        otras = [x for x in de_la_sala if x is not g and not x["bajando"]]
        to = max(0, min(len(otras), to))
        lugar = lambda x: next(i for i, y in enumerate(fila) if y is x)
        fila.pop(lugar(g))
        if to < len(otras):
            fila.insert(lugar(otras[to]), g)
        else:
            fila.insert(lugar(otras[-1]) + 1 if otras else len(fila), g)
        bump()


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
        sala.votos.pop(item_id, None)
        sala.fijas.discard(item_id)
        bump(sala)


def votar(sala, body):
    """👍 a una de la cola: sube hasta quedar debajo de las que tienen tantos votos como ella (o
    más). Un voto por persona y por canción, y no a la que pidió uno mismo: si no, cada uno
    subiría la suya. A las que puso en su lugar alguien con el PIN (⤒, o arrastrándola) no les pasa por arriba."""
    item_id, client = str(body.get("id", "")), str(body.get("client", ""))[:64]
    if not client:
        raise ApiError(400, "Falta el identificador; recargá la página")
    with lock:
        q = sala.queue
        i = next((k for k, x in enumerate(q) if x["id"] == item_id), None)
        if i is None:
            raise ApiError(404, "Esa canción ya no está en la cola")
        if sala.owners.get(item_id) == client:
            raise ApiError(409, "Es la que pediste vos: la votan los demás")
        votos = sala.votos.setdefault(item_id, [])
        if client in votos:
            raise ApiError(409, "Ya la votaste")
        votos.append(client)
        cuantos = lambda x: len(sala.votos.get(x["id"], ()))
        j = i
        while j > 0 and q[j - 1]["id"] not in sala.fijas and cuantos(q[j - 1]) < len(votos):
            j -= 1
        q.insert(j, q.pop(i))
        bump(sala)


def votar_saltear(sala, body):
    """Un voto para saltear la que suena. Con VOTOS_SALTEAR (de personas distintas) pasa a la
    siguiente, como con ⏭. `id`: cuál es, para que un voto que llega tarde no caiga en otra.
    Queda anotado en el chat quién votó (`by`: su nombre), así que sin nombre no se vota."""
    client, name = str(body.get("client", ""))[:64], _nombre(body, "")
    if not client:
        raise ApiError(400, "Falta el identificador; recargá la página")
    if not VOTOS_SALTEAR:
        raise ApiError(409, "Acá no se saltea por votos (votos_para_saltear = 0 en rockola.toml)")
    if not name:   # (queda anotado quién votó: sin nombre no se vota)
        raise ApiError(400, "Poné tu nombre arriba primero")
    with lock:
        now = sala.now
        if not now or now["id"] != str(body.get("id", "")):
            raise ApiError(409, "Esa canción ya no está sonando")
        if client in sala.saltear:
            raise ApiError(409, "Ya votaste saltearla")
        sala.saltear.append(client)
        alcanza = len(sala.saltear) >= VOTOS_SALTEAR
        anotar(sala, "⏭", name, f"votó saltear “{now['title']}” ({len(sala.saltear)} de {VOTOS_SALTEAR})", client)
        bump(sala)
    if alcanza and advance(sala, {"finished": now["id"]}, registro=f"Salteada “{now['title']}”: la votaron {VOTOS_SALTEAR}"):
        avisar(f"Salteada “{now['title']}”: la votaron {VOTOS_SALTEAR}", sala)


def reaccionar(sala, body):
    """Una reacción (🔥 ❤️ 😴) a la que suena: una de cada una por persona y por canción. Se ven al
    lado del tema y, un instante, en las pantallas; cuando la canción termina quedan en las
    estadísticas (ver _sonar). Tocándola de nuevo se saca: `on` dice si queda puesta o no (sin
    `on`, lo contrario de como estaba). Queda anotado en el chat quién reaccionó: una vez por
    persona, reacción y canción (sacarla y volverla a poner no llena el chat). Por eso, para
    reaccionar hay que haber puesto el nombre (`by`); para sacar una reacción, no."""
    emoji, client = str(body.get("emoji", "")), str(body.get("client", ""))[:64]
    name = _nombre(body, "")
    if not client:
        raise ApiError(400, "Falta el identificador; recargá la página")
    if emoji not in REACCIONES:
        raise ApiError(400, "Esa reacción no existe")
    with lock:
        now = sala.now
        if not now or now["id"] != str(body.get("id", "")):
            raise ApiError(409, "Esa canción ya no está sonando")
        quienes = sala.reacciones.setdefault(emoji, [])
        estaba = client in quienes
        on = body.get("on")
        on = (not estaba) if on is None else bool(on)
        if on == estaba:
            return   # (ya estaba así: p. ej. dos toques seguidos)
        if on and not name:
            raise ApiError(400, "Poné tu nombre arriba primero")
        if not on:
            quienes.remove(client)
        else:
            quienes.append(client)
            sala.reaccion = {"n": (sala.reaccion or {}).get("n", 0) + 1, "emoji": emoji, "by": name}
            if (client, emoji) not in sala.anotadas:
                sala.anotadas.add((client, emoji))
                anotar(sala, emoji, name, f"reaccionó a “{now['title']}”", client)
        bump(sala)


def advance(sala, body, por=None, registro=None):
    """Termina `finished` y pasa a la siguiente.

    Es idempotente: si `finished` no es la que está sonando, no hace nada
    (evita saltear dos canciones si llegan dos avisos seguidos).

    `por`: quién la salteó a mano con ⏭ (su nombre; "": desde la rockola, con la botonera o el
    teclado). Queda anotado en el chat. None: terminó sola, o la saltearon los votos (`registro`:
    lo que se anota entonces).
    """
    finished = body.get("finished")
    with lock:
        now = sala.now
        if (now["id"] if now else None) != finished:
            return False
        if sala.queue:
            sig = sala.queue.pop(0)
            if sala.previstas:
                sala.previstas.pop(0)   # (la elegida que esa había desplazado)
        elif sala.previstas:
            sig = sala.previstas.pop(0)
        else:
            sig = cancion_al_azar(sala)
        if now is None and sig is None:
            return False   # no sonaba nada y sigue sin sonar: no cambió nada
        if now and por is not None:
            anotar(sala, "⏭", por, f"salteó “{now['title']}”" if por else f"Saltearon “{now['title']}” desde la rockola",
                   str(body.get("client", ""))[:64])
        elif now and registro:
            anotar(sala, "⏭", "", registro)
        _sonar(sala, sig)
        return True


def _sonar(sala, item):
    """Que suene `item` (o nada): la que sonaba pasa a "sonaron recién" (y a las estadísticas,
    con lo que llegó a sonar). Con el lock tomado."""
    if sala.now:
        cuentas = {e: len(sala.reacciones[e]) for e in REACCIONES if sala.reacciones.get(e)}
        if stats and salas.get(sala.id) is sala:   # (si la borraron, sus estadísticas ya se fueron)
            stats.anotar(sala.id, sala.now, progreso_ahora(sala)["t"])
            if cuentas:
                stats.anotar_reacciones(sala.id, sala.now, cuentas)
        # (las reacciones que tuvo quedan con ella en «sonaron recién»)
        sala.history = (sala.history + [dict(sala.now, reacciones=cuentas) if cuentas else sala.now])[-30:]
    sala.now = item
    if item and voz:
        voz.cancion_nueva(sala.id)   # (para los anuncios «cada X canciones», de cada sala)
    sala.saltear, sala.reacciones, sala.anotadas = [], {}, set()   # los votos para saltear y las reacciones eran de la otra
    if item:
        item.pop("reacciones", None)         # (si vuelve a sonar, empieza de cero)
        sala.owners.pop(item["id"], None)
        sala.votos.pop(item["id"], None)
        sala.fijas.discard(item["id"])
    sala.progress = {"t": 0, "d": 0, "playing": False}
    sala.trabajo, sala.lista = None, False   # (sin parlante: Director.mover la baja y la arranca)
    sala.reanudar = None
    if item:   # (si una elegida es la que acaba de sonar de otra forma, que no vuelva)
        sala.previstas = [p for p in sala.previstas if p["videoId"] != item["videoId"]]
    rellenar_previstas(sala)
    bump(sala)


def play_now(sala, body):
    """▶ ahora (con el PIN): corta la que suena (queda en "sonaron recién", como con ⏭) y pone
    esta. Si estaba en la cola, sale de ahí; el resto de la cola sigue igual. Queda anotado en
    el chat quién fue."""
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
        anotar(sala, "▶", name, f"puso “{meta['title']}”" + (f" (cortó “{sala.now['title']}”)" if sala.now else ""),
               str(body.get("client", ""))[:64])
        _sonar(sala, dict(meta, id=uuid.uuid4().hex[:10], by=name))


PREVISTAS = 3   # cuántas de las que vienen se muestran (con el aleatorio): lo que pidan va reemplazando a las primeras


def rellenar_previstas(sala, evitar=()):
    """Con el aleatorio prendido, la lista de próximas siempre muestra PREVISTAS temas: los que
    pidieron, y los que faltan para llegar a esa cantidad, ya elegidos al azar. Lo que piden
    reemplaza al primero elegido y deja los otros: [azar1, azar2, azar3] → [pedida, azar2, azar3].
    Cuando suena una pedida, se va también el elegido que había desplazado, y entra uno nuevo al
    final. `evitar`: videoIds que no tienen que volver a salir (ver recargar_previstas). Llamar con el lock tomado."""
    if not (sala.shuffle and director):
        sala.previstas = []
        return
    ocupadas = {i["videoId"] for i in sala.queue} | {p["videoId"] for p in sala.previstas} | set(evitar)
    if sala.now:
        ocupadas.add(sala.now["videoId"])
    while len(sala.previstas) < PREVISTAS:
        c = director.descargas.al_azar(ocupadas, sala.id)
        if not c:
            break
        ocupadas.add(c["videoId"])
        sala.previstas.append(dict(c, id=uuid.uuid4().hex[:10], by="al azar", azar=True))


def recargar_previstas(sala):
    """🔄 en las próximas: las elegidas al azar se cambian por otras (que no sean las de recién, si alcanzan las guardadas)."""
    with lock:
        if not (sala.shuffle and director):
            raise ApiError(409, "Hace falta el aleatorio (🔀) prendido")
        viejas = {p["videoId"] for p in sala.previstas}
        sala.previstas = []
        rellenar_previstas(sala, viejas)
        if len(sala.previstas) < PREVISTAS:   # (pocas guardadas: se completa con lo que haya)
            rellenar_previstas(sala)
        bump(sala)


def previstas_visibles(sala):
    """Las elegidas al azar que se muestran ahora: las que no quedaron tapadas por lo que pidieron."""
    vids = {i["videoId"] for i in sala.queue} | ({sala.now["videoId"]} if sala.now else set())
    return [p for p in sala.previstas[len(sala.queue):] if p["videoId"] not in vids]


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
        rellenar_previstas(sala)
        bump(sala)
        if not sala.principal:
            guardar_salas()
    if sala.principal:
        guardar_ajustes(aleatorio=on)
    if on:
        arrancar_al_azar(sala)


def set_crossfade(sala, body):
    """Prende o apaga el crossfade (con el PIN): con él, cuando a una canción le quedan unos
    segundos, la que sigue va entrando mientras esa se va. Queda guardado."""
    if not director or not sala.principal:
        raise ApiError(409, "El crossfade es de la sala que suena en el parlante (en modo mpv)")
    if not CROSSFADE:
        raise ApiError(409, "El crossfade está sacado (crossfade = 0 en rockola.toml)")
    on = bool(body.get("on"))
    with lock:
        state["crossfade"] = on
        bump(sala)
    guardar_ajustes(crossfade=on)
    director.con_crossfade(on)


def skip(sala, por=""):
    """⏭ sin saber cuál está sonando (lo usan los botones físicos). `por`: como en advance."""
    with lock:
        now_id = sala.now["id"] if sala.now else None
    if now_id:
        advance(sala, {"finished": now_id}, por=por)


def previous(sala, por="", client=""):
    """⏮ como en cualquier reproductor: si la canción va por más de 5 s, vuelve a empezar;
    si no, pasa a la anterior (y la actual vuelve a quedar primera en la cola). Queda anotado en
    el chat quién fue (`por`: su nombre; "": desde la rockola)."""
    with lock:
        now, t = sala.now, progreso_ahora(sala)["t"]
    if now and t > 5 and director:
        if sala.principal:
            director.mpv.desde_el_principio()
        with lock:
            medir(sala.progress, 0)
            anotar(sala, "⏮", por, f"volvió a empezar “{now['title']}”" if por
                   else f"Volvieron a empezar “{now['title']}” desde la rockola", client)
            bump(sala)
        return
    with lock:
        if not sala.history:
            return
        anterior = sala.history.pop()
        if sala.now:
            sala.queue.insert(0, sala.now)
            sala.fijas.add(sala.now["id"])   # (vuelve primera: que siga después de la anterior)
        anotar(sala, "⏮", por, f"volvió a “{anterior['title']}”" if por
               else f"Volvieron a “{anterior['title']}” desde la rockola", client)
        sala.now = anterior
        anterior.pop("reacciones", None)     # (suena de nuevo: las reacciones y los votos, de cero)
        sala.saltear, sala.reacciones, sala.anotadas = [], {}, set()
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
        sala.fijas.add(item_id)   # (la pusieron ahí a mano: los votos no le pasan por arriba)
        bump(sala)


def move_to(sala, body):
    """Mueve una canción de la cola a la posición `to` (0 = la próxima). Con `before` (el id de
    la que tiene que quedarle justo después), va delante de esa, esté donde esté ahora: al
    arrastrarla, la cola pudo haber cambiado mientras tanto (terminó un tema, pidieron otro) y
    `to` ya no ser el lugar donde la soltaron. Si esa ya no está en la cola, vale `to`."""
    item_id = str(body.get("id", ""))
    try:
        to = int(body.get("to", 0))
    except (TypeError, ValueError):
        raise ApiError(400, "Posición inválida")
    antes_de = body.get("before")
    with lock:
        q = sala.queue
        idx = next((i for i, x in enumerate(q) if x["id"] == item_id), None)
        if idx is None:
            return
        item = q.pop(idx)
        if antes_de is not None:
            to = next((i for i, x in enumerate(q) if x["id"] == str(antes_de)), to)
        q.insert(max(0, min(len(q), to)), item)
        sala.fijas.add(item_id)   # (la pusieron ahí a mano: los votos no le pasan por arriba)
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
        sala.fijas.clear()   # (ya ninguna está donde la habían puesto)
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
        return dict(e, rooms=[s.publica() for s in salas.values()], mode=state["mode"], online=state["online"],
                    weather=state["weather"])


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
        from reproductor import Descargas
        self.args_mpv = list(MPV_ARGS)
        if NORMALIZAR:
            self.args_mpv.append("--af=lavfi=[dynaudnorm=f=500:g=31:p=0.95]")
        self.descargas = Descargas(carpeta() / "canciones", limite=GUARDAR_GB * 1024 ** 3,
                                   al_guardar=self.al_guardar, sala_vieja=PRINCIPAL,
                                   otra_version=version_de_audio)
        self.numeros = itertools.count(1)
        # Para el crossfade (ver fundir): otro mpv, que se levanta recién cuando se prende. En un
        # crossfade arranca la canción que sigue y pasa a ser `self.mpv`; el que tocaba la
        # anterior queda de `otro`, para el próximo.
        self.otro = None
        self.lock_otro = threading.Lock()   # (levantarlo o cerrarlo, de a uno)
        self.fundido = None                 # el crossfade en curso: {"sale": id, "entra": id}
        self.pausado = False
        guardado = leer_ajustes().get("volumen")
        inicial = round(max(0, min(130, guardado))) if isinstance(guardado, (int, float)) else VOLUME
        self.mpv = self._mpv(inicial)   # el que toca lo que suena
        self.mpv.iniciar()             # (desde acá llegan sus avisos: lo de arriba ya tiene que estar)
        self.ultimo_error = None
        with lock:
            state["volume"] = inicial

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

    def _mpv(self, volumen, numero=0):
        """Un mpv cuyos avisos dicen de cuál vienen (con el crossfade hay dos)."""
        from reproductor import Mpv
        m = Mpv(lambda item, error: self.al_terminar(item, error, m),
                lambda nombre, valor: self.al_cambiar(nombre, valor, m),
                volumen, self.args_mpv, al_reiniciar=lambda: self.al_reiniciar(m), numero=numero)
        return m

    def cerrar(self):
        for m in (self.mpv, self.otro):
            if m is not None:
                m.cerrar()

    # --- eventos de mpv (llegan desde el hilo lector del socket: nada lento acá)

    def al_terminar(self, item_id, error, mpv=None):
        if mpv is not None and mpv is not self.mpv:
            return   # el que se va en un crossfade: su canción ya pasó a «sonaron recién»
        p = self.principal
        with lock:
            now = p.now
            titulo = now["title"] if now and now["id"] == item_id else "?"
            f = self.fundido
            entraba = bool(error) and f is not None and f["entra"] == item_id and not f.get("fallo")
            if entraba:
                f["fallo"] = True   # (una sola vez: si vuelve a fallar, es la canción)
        if entraba:
            # La que entraba en un crossfade no pudo sonar mientras sonaba la otra: casi seguro la
            # salida de audio no deja sonar dos cosas a la vez (pasa sin PipeWire ni PulseAudio).
            # Se calla la que se iba, para soltar la salida; la que entraba se carga de nuevo
            # (el Director ve que mpv no tiene nada pedido); y el crossfade queda apagado, para
            # que no pase con cada tema.
            from reproductor import ErrorMpv
            try:
                self.otro.detener()
            except ErrorMpv:
                pass
            with mpv.lock:
                mpv.pedido = None
            with lock:
                state["crossfade"] = False
                bump(p)
            guardar_ajustes(crossfade=False)
            avisar("Apagué el crossfade: esta salida de audio no deja sonar dos canciones a la vez", p)
            return
        # Primero avanzar y después avisar: si no, el aviso despierta a la pre-descarga
        # con la canción fallida todavía como actual, y la vuelve a bajar.
        if advance(p, {"finished": item_id}) and error:
            avisar(f"Salteada “{titulo}”: {error}", p)

    def al_guardar(self):
        """Quedó guardada una canción: que las páginas actualicen la cuenta y la lista."""
        with lock:
            bump()
        averiguar_generos()   # (el de su artista, si todavía no se sabe)
        with lock:   # (con una guardada nueva puede haber de dónde elegir las que vienen)
            for s in salas.values():
                if s.shuffle and len(s.previstas) < PREVISTAS:
                    rellenar_previstas(s)
                    bump(s)
        copiar_tapas()        # (y que quede una copia de su tapa…)
        copiar_letras()       # (…y de su letra)

    def al_reiniciar(self, mpv=None):
        """mpv se cayó y se levantó de nuevo: lo que sonaba se perdió.

        El aviso cambia el estado, así que el Director se despierta y la vuelve a cargar, en el
        segundo en que iba (el último que se midió).
        """
        if mpv is not None and mpv is not self.mpv:
            return   # (el otro: a lo sumo se perdió la cola de la que se iba)
        p = self.principal
        with lock:
            if p.now is not None and p.reanudar is None and p.progress["t"]:
                p.reanudar = {"id": p.now["id"], "t": p.progress["t"], "pausada": self.pausado}
        avisar("El reproductor se reinició; retomando la canción", p)

    def al_cambiar(self, nombre, valor, mpv=None):
        if mpv is not None and mpv is not self.mpv:
            return   # el otro mpv: lo suyo no es lo que suena
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
            elif nombre == "volume" and valor is not None and not self.fundiendo() and not getattr(self, "bajada", False):
                # (durante un crossfade los dos mpv van subiendo y bajando: el volumen es el del estado)
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

    def bucle_guardar(self, olvidar=60, pausa=1, sin_red=30):
        """Las que pidieron guardar sin ponerlas en la cola (state["saving"]): las baja de a
        una, en el orden de la lista (que se cambia desde ⬇ descargas: ver mover_guardado).
        Mientras se baja una que tiene que sonar espera, que eso va primero (y así no hay más de
        dos descargas a la vez). Las que fallan quedan `olvidar` segundos en la lista, con su
        error, para que la página lo muestre. Las que esperan quedan anotadas en colas.json: si
        la rockola se reinicia, siguen (ver retomar_colas).

        Si una falla y resulta que no hay internet (p. ej. recién prendida la Raspberry, con el
        wifi todavía sin conectar), no es que esa canción no se pueda bajar: quedan todas
        esperando y se prueba de nuevo cada `sin_red` segundos.
        """
        avisado = False   # (que la consola no se llene: un aviso por corte)
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
                # Mientras esperaba pudieron cancelarla, o poner otra adelante (⤒ ↑): la primera de ahora.
                sig = next((g for g in state["saving"] if not g["error"]), None)
                if sig is None:
                    continue
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
            cortada = bool(error) and not hay_internet()   # (sin el lock: puede tardar unos segundos)
            with lock:
                if not error:   # que quede en las guardadas de cada sala que la pidió
                    for sid in sig["salas"]:
                        if sid in salas:
                            self.descargas.sumar_a_sala(vid, sid)
                sig["bajando"] = False
                cancelada = not sig["salas"]
                if cancelada or not error:
                    state["saving"] = [g for g in state["saving"] if g is not sig]
                elif not cortada:
                    sig["error"], sig["hasta"] = error, time.time() + olvidar
                state["sin_red"] = cortada   # (si es eso, la canción sigue en su lugar, esperando)
                bump()
            if cancelada and not error:   # la cancelaron mientras se bajaba: no queda guardada
                self.descargas.descartar(vid, en_uso())
            elif cortada:
                if not avisado:
                    print(f"  ⚠ sin internet: las descargas esperan (pruebo cada {sin_red} s)", flush=True)
                avisado = True
                time.sleep(sin_red)
                with lock:
                    state["sin_red"] = False   # a probar de nuevo
                    bump()
                continue
            elif error:
                print(f"  ⚠ no pude guardar “{sig['title']}”: {error}", flush=True)
            avisado = False

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
        guardado = state["volume"]   # el último volumen que quedó en ajustes.json
        while True:
            time.sleep(1)
            v = state["volume"]
            if v is not None and v != guardado and not getattr(self, "bajada", False):
                guardado = v   # (se mira acá, de a uno por segundo: arrastrar la barra no escribe mil veces)
                guardar_ajustes(volumen=v)
            mpv = self.mpv   # (uno solo en toda la vuelta: un crossfade los cambia de lugar)
            pedido = mpv.pedido
            if pedido is None:
                sin_archivo = 0
                continue
            try:
                if not mpv.esta_vivo():
                    # mpv se cayó o se colgó: levantarlo (al_reiniciar hace que el
                    # Director vuelva a cargar la canción, en vez de saltearla).
                    mpv.asegurar()
                    continue
                t = mpv.posicion()
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
            self.quizas_fundir(pedido, t)

    # --- el volumen (el del estado; casi siempre es, tal cual, el de mpv)

    def fundiendo(self):
        """¿Hay un crossfade andando (los dos mpv sonando, uno subiendo y el otro bajando)?"""
        f = self.fundido
        return f is not None and f["entra"] is not None

    BAJA_MUSICA = 0.25   # cuánto queda la música mientras suena una voz (ver bajar_musica)

    def bajar_musica(self, on):
        """Mientras suena una voz (un anuncio, el micrófono) la música baja, y después vuelve. El
        volumen del estado no cambia: es el que se restaura."""
        self.bajada = bool(on)
        v = state["volume"] or 0
        self.mpv.fijar_volumen(v * self.BAJA_MUSICA if on else v)

    def fijar_volumen(self, valor):
        if getattr(self, "bajada", False):   # (con la música baja: se anota, y se aplica bajo)
            with lock:
                state["volume"] = round(max(0.0, min(130.0, float(valor))))
                bump(self.principal)
            return self.mpv.fijar_volumen(state["volume"] * self.BAJA_MUSICA)
        if not self.fundiendo():
            return self.mpv.fijar_volumen(valor)
        with lock:   # en pleno crossfade: queda en el estado, y fundir() lo va aplicando
            state["volume"] = round(max(0.0, min(130.0, float(valor))))
            bump(self.principal)

    def cambiar_volumen(self, delta):
        if not self.fundiendo() and not getattr(self, "bajada", False):
            return self.mpv.cambiar_volumen(delta)
        self.fijar_volumen((state["volume"] or 0) + delta)

    # --- crossfade: la que sigue va entrando mientras la que suena se va

    def con_crossfade(self, on):
        """Se prendió o se apagó desde la web: que el segundo mpv esté listo, o que se vaya."""
        threading.Thread(target=self._otro_listo if on else self._soltar_otro, daemon=True, name="crossfade").start()

    def _otro_listo(self):
        """El segundo mpv, andando. Devuelve False si no se pudo levantar."""
        from reproductor import ErrorMpv
        with self.lock_otro:
            try:
                if self.otro is None:
                    self.otro = self._mpv(0, next(self.numeros))
                self.otro.asegurar()
                return True
            except ErrorMpv as e:
                print(f"  ⚠ crossfade: no pude levantar el segundo mpv: {e}", flush=True)
                return False

    def _soltar_otro(self):
        """Con el crossfade apagado el segundo mpv no hace falta (en una Pi, es memoria)."""
        with self.lock_otro:
            if self.otro is not None and self.fundido is None and not state["crossfade"]:
                self.otro.cerrar()
                self.otro = None

    def quizas_fundir(self, now_id, t):
        """Cada segundo, desde bucle_progreso: si a la que suena le queda lo que dura el
        crossfade y hay con qué seguir, lo arranca (en otro hilo: ver fundir)."""
        if not (state["crossfade"] and CROSSFADE) or self.fundido is not None:
            return
        p = self.principal
        with lock:
            now = p.now
            if self.fundido is not None or self.pausado or p.loading or not now or now["id"] != now_id:
                return
            d = p.progress.get("d") or now["duration"]
            if d < 4 * CROSSFADE or not 1.5 <= d - t <= CROSSFADE + 1:
                return   # es muy corta, o todavía no es el momento (o ya es tarde)
            if p.queue:
                if not self.descargas.lista(p.queue[0]["videoId"]):
                    return   # la que sigue no terminó de bajar: esta vez, sin crossfade
            elif not p.shuffle:
                return   # no sigue nada
            self.fundido = {"sale": now_id, "entra": None}
        threading.Thread(target=self.fundir, args=(now_id,), daemon=True, name="crossfade").start()

    def fundir(self, sale_id):
        """Un crossfade. El otro mpv arranca la canción que sigue, callado, y pasa a ser «el
        mpv» (para el estado ya suena esa: la anterior queda en «sonaron recién»). Después, en
        lo que le queda a la anterior, uno sube y el otro baja, sin que el volumen del conjunto
        se note cambiar. Si en el medio saltean, vuelven atrás o ponen otra, se corta ahí: la
        anterior calla y la que haya quedado sigue al volumen de siempre. Con ⏸ esperan los dos."""
        from reproductor import ErrorMpv
        p = self.principal
        entra = sale = None
        try:
            if not self._otro_listo():
                return
            self.otro.fijar_volumen(0)
            with lock:   # todo junto, para que nadie vea un estado a medias (ni se cruce un ⏭)
                now = p.now
                if self.pausado or not now or now["id"] != sale_id or self.mpv.pedido != sale_id:
                    return
                # (la misma que elegiría advance: lo pedido, y si no, la primera de las que se mostraban como próximas)
                sig = p.queue[0] if p.queue else p.previstas[0] if p.previstas else cancion_al_azar(p)
                if sig is None or not self.descargas.lista(sig["videoId"]):
                    return
                if p.queue:
                    p.queue.pop(0)
                    if p.previstas:
                        p.previstas.pop(0)
                elif p.previstas:
                    p.previstas.pop(0)
                d = p.progress.get("d") or now["duration"]
                largo = max(0.5, min(CROSSFADE + 1, d - progreso_ahora(p)["t"] - 0.25))
                sale, entra = self.mpv, self.otro
                self.mpv, self.otro = entra, sale
                self.fundido = {"sale": sale_id, "entra": sig["id"]}
                _sonar(p, sig)   # (como advance: el Director ve otra canción y la carga, en `entra`)

            def sigue():   # ¿sigue siendo esa la que suena? (si no: ⏭, ⏮, ▶ ahora…)
                with lock:
                    return p.now is not None and p.now["id"] == sig["id"]

            espera = time.monotonic() + 5
            while entra.actual != sig["id"]:   # que el Director la cargue y mpv la arranque
                if not sigue() or time.monotonic() > espera:
                    return
                time.sleep(0.02)
            # Uno baja como un coseno y el otro sube como un seno: así la potencia de los dos
            # juntos no cambia. La raíz cúbica es porque el volumen de mpv va al cubo.
            hecho, antes, sale_en_pausa = 0.0, time.monotonic(), False
            while hecho < largo and sigue() and entra.pedido == sig["id"]:
                if self.pausado != sale_en_pausa:   # ⏸ (o ▶): el que se va, lo mismo
                    sale.comando("set_property", "pause", self.pausado)
                    sale_en_pausa = self.pausado
                ahora = time.monotonic()
                if not self.pausado:   # (en pausa el crossfade no avanza)
                    hecho = min(largo, hecho + ahora - antes)
                    k, volumen = hecho / largo, state["volume"] or 0
                    entra.comando("set_property", "volume", volumen * math.sin(k * math.pi / 2) ** (1 / 3))
                    sale.comando("set_property", "volume", volumen * math.cos(k * math.pi / 2) ** (1 / 3))
                antes = ahora
                time.sleep(0.05)
        except ErrorMpv as e:   # alguno de los dos no contestó: se termina acá (abajo queda todo en orden)
            print(f"  ⚠ crossfade: {e}", flush=True)
        finally:
            if sale is not None:   # llegaron a cambiar de lugar: que quede todo en orden, pase lo que pase
                volumen = state["volume"] or 0
                entra.volumen = volumen   # (si justo se cayó, que al levantarse arranque con el de siempre)
                # (con `comando`, no con fijar_volumen: si la rockola se está cerrando, que no lo levante de nuevo)
                for hacer in (sale.detener, lambda: sale.comando("set_property", "pause", False),
                              lambda: entra.comando("set_property", "volume", volumen)):
                    try:
                        hacer()
                    except ErrorMpv:
                        pass
            time.sleep(0.3)   # (que lleguen los últimos avisos de volumen de mpv antes de volver a escucharlos)
            self.fundido = None
            if sale is not None and round(entra.volumen) != (state["volume"] or 0):
                try:   # (cambiaron el volumen justo en ese ratito: que mpv quede como dice el estado)
                    entra.comando("set_property", "volume", state["volume"] or 0)
                except ErrorMpv:
                    pass
            if not state["crossfade"]:
                self._soltar_otro()

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


https_activo = False   # si el puerto https está andando


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

    def acepta_gzip(self):
        return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def send_bytes(self, data, ctype, code=200, cache=False, gz=None):
        """`cache`: False, que el navegador no lo guarde (lo de siempre); True, que decida él; un
        número, que lo guarde esos segundos sin volver a preguntar.
        `gz`: lo mismo ya comprimido (las páginas, ver pagina). Si no, lo que es texto y pesa
        (el estado, las guardadas, una búsqueda, el recap) se comprime acá, si el navegador lo
        acepta: es mucho menos para mandar por el wifi, y a la rockola no le cuesta nada (nivel 1)."""
        comprimido = False
        if self.acepta_gzip():
            if gz is None and len(data) >= COMPRIMIR_DESDE and ctype.startswith(("application/json", "text/", "image/svg")):
                gz = gzip.compress(data, compresslevel=1)
            if gz is not None and len(gz) < len(data):
                data, comprimido = gz, True
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if comprimido:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        if not cache:
            self.send_header("Cache-Control", "no-store")
        elif cache is not True:   # (segundos: algo que no cambia nunca, como la letra)
            self.send_header("Cache-Control", f"public, max-age={int(cache)}, immutable")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, code=200):
        texto = json.dumps(obj)
        if tapas:   # las tapas que están copiadas acá, con la dirección de la copia (ver tapas.py)
            texto = tapas.en_json(texto)
        self.send_bytes(texto.encode(), "application/json; charset=utf-8", code)

    def send_page(self, name):
        data, gz = pagina(name)
        self.send_bytes(data, "text/html; charset=utf-8", gz=gz)

    def send_audio(self, ruta, tipo=None):
        """Un archivo de audio, entero o el pedazo que diga Range: así el navegador puede
        arrancar por cualquier parte de la canción. `tipo`: su Content-Type, si se sabe."""
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
            self.send_header("Content-Type", tipo or TIPOS_AUDIO.get(Path(ruta).suffix.lower(), "application/octet-stream"))
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
            if u.path in ("/", "/recap", "/descargas", "/voz"):   # (la misma página, abierta en 📊 recap o en ⬇ descargas)
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
            fuente = FUENTE.fullmatch(u.path)
            if fuente:   # la letra de las páginas: la sirve la rockola (no Google Fonts), y el navegador la guarda
                ruta = STATIC / "fuentes" / fuente[1]
                if not ruta.is_file():
                    raise ApiError(404, "No existe")
                return self.send_bytes(ruta.read_bytes(), "font/woff2", cache=UN_ANIO)
            if u.path.startswith("/tapas/"):   # la copia de una tapa (la dirección dice cuál es: no cambia nunca)
                copia = tapas and tapas.archivo(u.path.removeprefix("/tapas/"))
                if not copia:
                    raise ApiError(404, "No existe")
                try:
                    return self.send_bytes(copia[0].read_bytes(), copia[1], cache=UN_ANIO)
                except OSError:
                    raise ApiError(404, "No existe")
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
                return self.send_json({"tvs": teles.publicas(), "rooms": rooms, "mode": state["mode"],
                                       "online": state["online"], "weather": state["weather"]})
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
            if u.path == "/api/voz-mic-stream":   # 📻 radio: el micrófono en vivo
                return self.voz_mic_stream(qs)
            if u.path.startswith("/api/voz-audio/"):   # 📻 radio: el audio de un anuncio que suena
                aid = u.path.removeprefix("/api/voz-audio/")
                ruta = voz and ID_ANUNCIO.fullmatch(aid) and voz.archivo(aid)
                if not ruta or not any(e["id"] == aid for s in list(salas) for e in [voz.en_el_aire(s)]):
                    raise ApiError(404, "Ese anuncio no está sonando")
                return self.send_audio(ruta, voz.tipo(ruta))
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
                if vista == "artistas":  # los artistas, con cuántas tiene cada uno
                    return self.send_json(director.descargas.artistas(desde, n, sala=sid))
                if vista == "artista":   # lo de uno: sus temas y sus álbumes
                    return self.send_json(artista_guardado(sid, qs.get("artista", [""])[0][:200]))
                ver = director.descargas.albumes if vista == "albumes" else director.descargas.listado
                return self.send_json(ver(qs.get("q", [""])[0][:100], desde, n, sala=sid))
            if u.path == "/api/stats":   # el recap
                return self.send_json(recap(qs.get("sala", [""])[0], qs.get("periodo", [""])[0][:10],
                                            qs.get("persona", [""])[0]))
            if u.path == "/api/info":
                return self.send_json({"url": url_publica(), "mode": state["mode"],
                                       "pin": bool(PIN), "https": HTTPS_PORT if https_activo else 0,
                                       "voz": voz is not None})
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
            if path in ("/api/voz-chunk", "/api/voz-anuncio"):   # audio crudo, no JSON
                return self.voz_crudo(path)
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
            if path == "/api/download-playlist":   # …y con una playlist de YouTube (arma una ♫ playlist)
                r = save_playlist(sala, body)
                return self.send_json(dict(r, state=public_state(sala, client)))
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
            if path in ("/api/vote", "/api/vote-skip", "/api/react"):   # 👍 en la cola, ⏭ por votos, 🔥 ❤️ 😴
                {"/api/vote": votar, "/api/vote-skip": votar_saltear, "/api/react": reaccionar}[path](sala, body)
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
            if path.startswith("/api/voz-"):
                try:
                    voz_accion(sala, path, body)
                except ErrorVoz as e:
                    raise ApiError(e.code, e.msg)
                return self.send_json(voz_estado(sala))
            if path == "/api/room-delete":
                borrar_sala(sala)
                return self.send_json({"ok": True})
            if path == "/api/library-remove":   # 🗑 una guardada, a mano
                texto = quitar_guardada(sala, body)
                return self.send_json({"text": texto, "state": public_state(sala, client)})
            # Quién cambia la que suena queda anotado en el chat: con su nombre o, sin nombre,
            # "Alguien" (desde esta máquina: "desde la rockola", que es la botonera o el teclado).
            quien = _nombre(body, "" if self.is_local else "Alguien")
            if path == "/api/play-now":
                play_now(sala, body)
            elif path == "/api/next":
                # (con `by`, es ⏭ a mano; sin `by`, el reproductor del modo navegador avisando que terminó)
                advance(sala, body, por=quien if "by" in body else None)
            elif path == "/api/skip":
                skip(sala, quien)
            elif path == "/api/prev":
                previous(sala, quien, str(client or "")[:64])
            elif path == "/api/first":
                move_first(sala, body)
            elif path == "/api/move":
                move_to(sala, body)
            elif path == "/api/download-move":   # ⤒ ↑ ↓ en ⬇ descargas: el orden en que se bajan
                mover_guardado(sala, body)
            elif path == "/api/remove-playlist":
                remove_playlist(sala, body)
            elif path == "/api/shuffle":
                set_shuffle(sala, body)
            elif path == "/api/shuffle-queue":
                mezclar_cola(sala)
            elif path == "/api/reroll":
                recargar_previstas(sala)
            elif path == "/api/crossfade":
                set_crossfade(sala, body)
            elif path == "/api/pause":
                pause(sala)
            elif path == "/api/volume":
                if not sala.principal:
                    raise ApiError(409, "Esta sala no tiene parlante: cada uno maneja su volumen")
                if not director:
                    raise ApiError(409, "En modo navegador, pausa y volumen se manejan desde /player")
                if "value" in body:
                    director.fijar_volumen(float(body["value"]))
                else:
                    director.cambiar_volumen(float(body.get("delta") or 0))
            else:
                raise ApiError(404, "No existe")
            return self.send_json(public_state(sala, client))

        self.handle_errors(route)

    def voz_crudo(self, path):
        """Audio crudo (un pedacito del micrófono, o un anuncio entero): con el PIN de la sala (`sala`, en la
        dirección). El contenido va en el cuerpo; lo demás, en la dirección."""
        qs = parse_qs(urlparse(self.path).query)
        sala = sala_de(qs.get("sala", [""])[0])
        if not self.es_admin(sala):
            raise ApiError(403, "Para eso hace falta el PIN")
        if not voz:
            raise ApiError(409, "La voz sólo anda en modo mpv")
        import voz as v
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "Content-Length inválido")
        tope = v.MAX_CHUNK if path == "/api/voz-chunk" else v.MAX_ANUNCIO
        if n <= 0 or n > tope:
            raise ApiError(413, "Tamaño inválido")
        data = self.rfile.read(n)
        try:
            if path == "/api/voz-chunk":
                voz.mic_pedazo(sala.id, data)
            else:
                voz.anuncio_nuevo(sala.id, data, qs.get("nombre", [""])[0], qs.get("cada_min", ["0"])[0],
                                  qs.get("cada_canciones", ["0"])[0], qs.get("by", [""])[0])
        except ErrorVoz as e:
            raise ApiError(e.code, e.msg)
        return self.send_json(voz_estado(sala))

    def voz_mic_stream(self, qs):
        """📻 radio: lo que va llegando del micrófono de la sala, desde el pedazo `desde` (sin PIN: es
        lo que se oye por el parlante). Si pide desde 0, el primero (con el encabezado) va adelante.
        Espera unos segundos a que haya algo nuevo; 410 si el micrófono no está abierto."""
        if not voz:
            raise ApiError(409, "La voz sólo anda en modo mpv")
        sala = sala_de(qs.get("sala", [""])[0])
        try:
            desde = max(0, int(qs.get("desde", ["0"])[0]))
        except ValueError:
            desde = 0
        r = voz.mic_leer(sala.id, desde)
        if r is None:
            raise ApiError(410, "El micrófono está cerrado")
        init, nuevos, seq = r
        # (sumándose a mitad de camino: el encabezado, y de ahí en adelante los más nuevos)
        data = (init if desde == 0 and init else b"") + b"".join(d for n, d in nuevos if not (desde == 0 and n == 1))
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Seq", str(seq))
        self.send_header("Access-Control-Expose-Headers", "X-Seq")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

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
                                   "/api/audio", "/api/voz-audio", "/api/voz-mic-stream", "/fuentes/", "/tapas/")):
            return
        super().log_message(fmt, *args)


def main():
    global director, stats, listas, teles, tapas, letras_guardadas, voz, https_activo, PORT, IP_FIJA
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
    state["crossfade"] = modo == "mpv" and CROSSFADE > 0 and bool(leer_ajustes().get("crossfade"))

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
        with lock:   # (con el aleatorio prendido de antes: ya elegir las que vienen)
            for s in salas.values():
                rellenar_previstas(s)
        if state["crossfade"]:
            director.con_crossfade(True)   # (el segundo mpv, ya listo para el primer cambio de tema)
        from reproductor import _lanzar
        from voz import Voz
        voz = Voz(carpeta() / "anuncios", director.bajar_musica, _lanzar, MPV_ARGS, al_cambiar=_bump_sala,
                  mpv="mpv", principal=PRINCIPAL)
        averiguar_generos()   # (los de las guardadas que falten: se le pregunta a iTunes de a uno, sin apuro)
        if director.descargas.limite:   # (si se guardan canciones)
            from tapas import Tapas
            try:
                tapas = Tapas(carpeta() / "tapas", hay_red=lambda: state["online"] is not False)
                copiar_tapas(limpiar=True)   # (las que falten: se bajan de a una, sin apuro)
            except OSError as e:   # sin copias, pero que suene
                print(f"  ⚠ no pude preparar la carpeta de las tapas ({carpeta() / 'tapas'}): {e}", flush=True)
            from letras import Letras
            try:
                letras_guardadas = Letras(carpeta() / "letras", _copiar_letra, hay_red=lambda: state["online"] is not False)
                copiar_letras(limpiar=True)   # (las que falten: se buscan de a una, muy despacio)
            except OSError as e:
                print(f"  ⚠ no pude preparar la carpeta de las letras ({carpeta() / 'letras'}): {e}", flush=True)
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
    threading.Thread(target=bucle_internet, daemon=True, name="internet").start()
    if CLIMA is not False:
        threading.Thread(target=bucle_clima, daemon=True, name="clima").start()
        threading.Thread(target=bucle_temperatura, daemon=True, name="temperatura").start()

    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    if HTTPS_PORT and modo == "mpv":
        from voz import certificado
        cert = certificado(carpeta() / "https", IP_FIJA or lan_ip())
        if cert:
            try:
                seguro = ThreadingHTTPServer(("0.0.0.0", HTTPS_PORT), Handler)
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(*map(str, cert))
                seguro.socket = ctx.wrap_socket(seguro.socket, server_side=True)
                threading.Thread(target=seguro.serve_forever, daemon=True, name="https").start()
                https_activo = True
            except (OSError, ssl.SSLError) as e:
                print(f"  ⚠ no pude abrir el puerto https {HTTPS_PORT}: {e}", flush=True)

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
