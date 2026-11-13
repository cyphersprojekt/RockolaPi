"""Las playlists que se arman en la rockola (♫), por sala.

Cualquiera arma una (queda como suya: la reconoce por el identificador de su navegador, el mismo
con el que puede quitar sus temas de la cola); la cambia quien la armó, o alguien con el PIN de la
sala. Cargarla entera a la cola pide el PIN, como una de YouTube (eso lo decide server.py).

Quedan en listas.json, en la carpeta de la rockola: sobreviven a los reinicios. Cada tema guarda
sus datos (título, artista, duración, tapa, álbum), así la lista se ve sin preguntarle a YouTube.
"""
import json
import os
import threading
import time
import unicodedata
import uuid
from pathlib import Path

MAX_LISTAS = 50        # por sala
MAX_TEMAS = 200        # por playlist (lo mismo que se carga, como mucho, de una de YouTube)
MAX_NOMBRE = 40
DATOS = ("videoId", "title", "artist", "duration", "thumb", "album")   # lo que se guarda de cada tema


class ErrorLista(Exception):
    """Algo que no se puede hacer con una playlist. `code`: el código HTTP para la página."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def _orden(texto):
    """Para ordenar y comparar nombres sin fijarse en mayúsculas ni acentos."""
    t = unicodedata.normalize("NFKD", str(texto).casefold())
    return "".join(c for c in t if not unicodedata.combining(c))


def _nombre(nombre):
    n = " ".join(str(nombre or "").split())[:MAX_NOMBRE].strip()
    if not n:
        raise ErrorLista(400, "Ponele un nombre a la playlist")
    return n


def _tema(c):
    """Un tema, sólo con lo que se guarda, o None si no está bien."""
    if not isinstance(c, dict) or not isinstance(c.get("videoId"), str) or len(c["videoId"]) != 11:
        return None
    try:
        duracion = int(c.get("duration") or 0)
    except (TypeError, ValueError):
        return None
    t = {k: str(c.get(k) or "")[:1000] for k in DATOS if k != "duration"}
    t["duration"] = duracion
    return t


def _leida(d):
    """Una playlist leída de listas.json, o None si no está bien."""
    try:
        lista = {"id": str(d["id"]), "sala": str(d["sala"]), "nombre": _nombre(d["nombre"]),
                 "dueno": str(d.get("dueno") or "")[:64], "por": str(d.get("por") or "")[:30],
                 "creada": float(d.get("creada") or 0), "cambiada": float(d.get("cambiada") or 0)}
        temas = d.get("temas")
    except (KeyError, TypeError, ValueError, ErrorLista):
        return None
    vistos, lista["temas"] = set(), []
    for t in map(_tema, temas if isinstance(temas, list) else []):
        if t and t["videoId"] not in vistos and len(lista["temas"]) < MAX_TEMAS:
            vistos.add(t["videoId"])
            lista["temas"].append(t)
    return lista


class Listas:
    def __init__(self, ruta):
        self.ruta = Path(ruta)
        self.lock = threading.Lock()
        self.listas = {}        # id -> playlist
        self.versiones = {}     # sala -> sube con cada cambio en sus playlists (las páginas las piden de nuevo)
        self._leer()

    # --- el archivo

    def _leer(self):
        try:
            datos = json.loads(self.ruta.read_text())
            if not isinstance(datos, dict) or not isinstance(datos.get("listas"), list):
                raise ValueError("no tiene «listas»")
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            # Que no se pise al guardar la próxima: queda aparte, por si se puede rescatar algo.
            aparte = self.ruta.with_name(f"listas.rota-{time.strftime('%Y%m%d-%H%M%S')}.json")
            try:
                os.replace(self.ruta, aparte)
                print(f"  ⚠ no pude leer {self.ruta} ({e}): la dejé en {aparte.name} y arranco sin playlists",
                      flush=True)
            except OSError:
                print(f"  ⚠ no pude leer {self.ruta} ({e})", flush=True)
            return
        for lista in map(_leida, datos["listas"]):
            if lista:
                self.listas[lista["id"]] = lista

    def _guardar(self):
        """Con el lock tomado: escribe listas.json (entero, de una: o queda el de antes o el nuevo)."""
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.ruta.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "listas": list(self.listas.values())}, ensure_ascii=False))
            os.replace(tmp, self.ruta)
        except OSError as e:
            print(f"  ⚠ no pude guardar {self.ruta}: {e}", flush=True)

    def _cambio(self, lista):
        lista["cambiada"] = time.time()
        self.versiones[lista["sala"]] = self.versiones.get(lista["sala"], 0) + 1
        self._guardar()

    # --- lo que ve la página

    @staticmethod
    def _resumen(lista, client):
        temas = lista["temas"]
        return {"id": lista["id"], "name": lista["nombre"], "by": lista["por"],
                "mine": bool(client) and lista["dueno"] == client,
                "count": len(temas), "duration": sum(t["duration"] for t in temas),
                "thumb": next((t["thumb"] for t in temas if t["thumb"]), "")}

    def _completa(self, lista, client):
        return dict(self._resumen(lista, client), songs=[dict(t) for t in lista["temas"]])

    def version(self, sala):
        with self.lock:
            return self.versiones.get(sala, 0)

    def de_sala(self, sala, client=""):
        """Las playlists de `sala`, por nombre (sin los temas)."""
        with self.lock:
            propias = sorted((l for l in self.listas.values() if l["sala"] == sala),
                             key=lambda l: (_orden(l["nombre"]), l["creada"]))
            return {"v": self.versiones.get(sala, 0), "lists": [self._resumen(l, client) for l in propias]}

    def una(self, sala, lid, client=""):
        """Una playlist, con sus temas."""
        with self.lock:
            return self._completa(self._de(sala, lid), client)

    def temas(self, sala, lid):
        """(nombre, temas) de una playlist: para cargarla a la cola."""
        with self.lock:
            lista = self._de(sala, lid)
            return lista["nombre"], [dict(t) for t in lista["temas"]]

    # --- cambios

    def _de(self, sala, lid):
        lista = self.listas.get(str(lid))
        if lista is None or lista["sala"] != sala:
            raise ErrorLista(404, "Esa playlist ya no existe")
        return lista

    @staticmethod
    def _permiso(lista, client, admin):
        if not (admin or (client and lista["dueno"] == client)):
            raise ErrorLista(403, f"«{lista['nombre']}» la puede cambiar sólo quien la armó"
                                  + (f" ({lista['por']})" if lista["por"] else "") + ", o alguien con el PIN")

    def _libre(self, sala, nombre, salvo=None):
        if any(l["sala"] == sala and l["id"] != salvo and _orden(l["nombre"]) == _orden(nombre)
               for l in self.listas.values()):
            raise ErrorLista(409, f"Ya hay una playlist «{nombre}» en esta sala")

    def puede(self, sala, lid, client, admin):
        """Si no la puede cambiar, ErrorLista (para chequearlo antes de ir a buscar los temas)."""
        with self.lock:
            self._permiso(self._de(sala, lid), client, admin)

    def crear(self, sala, nombre, por, client):
        nombre = _nombre(nombre)
        if not client:
            raise ErrorLista(400, "Falta el identificador; recargá la página")
        with self.lock:
            self._libre(sala, nombre)
            if sum(l["sala"] == sala for l in self.listas.values()) >= MAX_LISTAS:
                raise ErrorLista(429, f"Ya hay {MAX_LISTAS} playlists en esta sala: borren alguna que no se use")
            lista = {"id": uuid.uuid4().hex[:10], "sala": sala, "nombre": nombre, "dueno": client,
                     "por": " ".join(str(por or "").split())[:30], "creada": time.time(), "cambiada": 0,
                     "temas": []}
            self.listas[lista["id"]] = lista
            self._cambio(lista)
            return self._completa(lista, client)

    def renombrar(self, sala, lid, nombre, client, admin):
        nombre = _nombre(nombre)
        with self.lock:
            lista = self._de(sala, lid)
            self._permiso(lista, client, admin)
            if lista["nombre"] != nombre:
                self._libre(sala, nombre, salvo=lista["id"])
                lista["nombre"] = nombre
                self._cambio(lista)
            return self._completa(lista, client)

    def borrar(self, sala, lid, client, admin):
        """Devuelve su nombre."""
        with self.lock:
            lista = self._de(sala, lid)
            self._permiso(lista, client, admin)
            del self.listas[lista["id"]]
            self._cambio(lista)
            return lista["nombre"]

    def sumar(self, sala, lid, canciones, client, admin):
        """Suma `canciones` al final (las que no estaban). Devuelve (cuántas se sumaron, cuántas ya
        estaban, cuántas no entraron, la playlist)."""
        with self.lock:
            lista = self._de(sala, lid)
            self._permiso(lista, client, admin)
            ya = {t["videoId"] for t in lista["temas"]}
            sumadas = repetidas = sin_lugar = 0
            for t in map(_tema, canciones):
                if not t:
                    continue
                if t["videoId"] in ya:
                    repetidas += 1
                elif len(lista["temas"]) >= MAX_TEMAS:
                    sin_lugar += 1
                else:
                    ya.add(t["videoId"])
                    lista["temas"].append(t)
                    sumadas += 1
            if sumadas:
                self._cambio(lista)
            return sumadas, repetidas, sin_lugar, self._completa(lista, client)

    def quitar(self, sala, lid, video_id, client, admin):
        with self.lock:
            lista = self._de(sala, lid)
            self._permiso(lista, client, admin)
            antes = len(lista["temas"])
            lista["temas"] = [t for t in lista["temas"] if t["videoId"] != video_id]
            if len(lista["temas"]) != antes:
                self._cambio(lista)
            return self._completa(lista, client)

    def mover(self, sala, lid, video_id, a, client, admin):
        """Lleva un tema a la posición `a` (0 = el primero)."""
        with self.lock:
            lista = self._de(sala, lid)
            self._permiso(lista, client, admin)
            temas = lista["temas"]
            i = next((i for i, t in enumerate(temas) if t["videoId"] == video_id), None)
            if i is not None and i != a:
                temas.insert(max(0, min(len(temas) - 1, a)), temas.pop(i))
                self._cambio(lista)
            return self._completa(lista, client)

    def borrar_sala(self, sala):
        """Borraron la sala: sus playlists también se van."""
        with self.lock:
            suyas = [lid for lid, l in self.listas.items() if l["sala"] == sala]
            for lid in suyas:
                del self.listas[lid]
            if suyas:
                self.versiones.pop(sala, None)
                self._guardar()
