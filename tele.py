"""Las salas de tv (📺): para mirar videos de YouTube entre varios, cada uno en su navegador.

Nada de esto pasa por el parlante ni se baja: cada página (/tv) muestra el video con el
reproductor de YouTube, y acá sólo se lleva la cuenta de la sala (qué video va, por qué segundo,
si está en pausa, la cola y el chat). Las páginas hacen que su reproductor vaya a la par.

El control es de todos, como un control remoto compartido: si alguien pausa, sigue, adelanta o
salta el video, les pasa a todos. La cola es como la de la rockola: cada uno quita lo suyo y, con
el PIN (el de la sala o el de la rockola, eso lo revisa server.py), lo de cualquiera.

El reloj de un video corre sólo mientras alguien lo mira, y arranca cuando la primera página avisa
que ya lo está viendo ("ready"): así nadie se pierde el principio por lo que tarde en cargar, y
si se van todos queda en espera, donde iba.

Las salas, con su cola y por dónde iba el video, quedan en tv.json, en la carpeta de la rockola:
sobreviven a los reinicios. El chat vive en memoria.
"""
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path

MAX_TELES = 8          # salas de tv
MAX_COLA = 100         # videos esperando en una sala
MAX_CHAT = 100         # mensajes que se recuerdan por sala
VENCE = 40             # segundos sin noticias de una página que miraba para darla por cerrada
                       # (la página pregunta el estado cada 25 s como mucho)
GRACIA = 2             # pasado el final, segundos que el reloj espera a que una página avise que terminó
FINAL = 10             # ese aviso vale si al reloj le falta menos que esto para el final
CERCA = 5              # al pausar, queda en el segundo de quien pausó si está a menos que esto del reloj
ADELANTE = 15          # al arrancar, el reloj no le cree a una página que va más adelantada que esto
MAX_ORDENES = 20       # del control, por persona, cada 10 segundos
DATOS = ("videoId", "title", "artist", "duration", "thumb", "live")   # lo que se guarda de cada video
# Errores del reproductor de YouTube que no se arreglan esperando: el video se saltea para todos.
NO_SE_VE = {100: "ya no existe o es privado", 101: "no deja verse fuera de YouTube",
            150: "no deja verse fuera de YouTube"}


class ErrorTele(Exception):
    """Algo que no se puede hacer en una sala de tv. `code`: el código HTTP para la página."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def _hora(s):
    """754 -> "12:34"."""
    s = int(s)
    h, m = s // 3600, s // 60 % 60
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def _nombre(by, sin="Anónimo"):
    """El nombre que puso alguien (`sin`: si no puso ninguno)."""
    return str(by or "").strip()[:30] or sin


def _video(v):
    """Un video, sólo con lo que se guarda, o None si no está bien. `duration` 0: en vivo."""
    if not isinstance(v, dict) or not isinstance(v.get("videoId"), str) or len(v["videoId"]) != 11:
        return None
    try:
        duracion = max(0, int(v.get("duration") or 0))
    except (TypeError, ValueError, OverflowError):
        return None
    d = {k: str(v.get(k) or "")[:1000] for k in ("videoId", "title", "artist", "thumb")}
    d["live"] = bool(v.get("live")) or not duracion
    d["duration"] = 0 if d["live"] else duracion
    return d


def _item(i):
    """Un video de la cola leído de tv.json (con su id y quién lo pidió), o None."""
    v = _video(i)
    if v is None or not isinstance(i.get("id"), str) or not i["id"]:
        return None
    return dict(v, id=i["id"][:20], by=_nombre(i.get("by")))


class Tele:
    """Una sala de tv: el video que va, su reloj, la cola y el chat."""

    def __init__(self, id, nombre, pin="", sal=""):
        self.id, self.nombre = id, nombre
        self.pin, self.sal = pin, sal      # el PIN de la sala, con sal y hash (como el de las otras salas)
        self.version = 0                   # sube con cada cambio; las páginas esperan a que cambie
        self.now = None                    # el video que va
        self.queue = []                    # los que siguen
        self.owners = {}                   # item id -> client id (quién lo pidió, para poder quitarlo)
        # El reloj: por dónde iba (`t`) cuando se midió (`at`), y si corre desde entonces.
        self.t, self.at, self.corre = 0.0, 0.0, False
        self.pausada = False               # alguien lo pausó…
        self.pauso = None                  # …y quién
        self.lista = False                 # alguna página ya lo está viendo: el reloj puede correr
        self.notice = None                 # {"id", "text", "err", "client"}: lo último que avisar
        self.chat = []                     # [{"id", "by", "text", "at", "client"}]
        self.viendo = {}                   # client -> cuándo se supo de su página (con el video a la vista)

    def publica(self):
        return {"id": self.id, "name": self.nombre}


class Teles:
    """Todas las salas de tv. Tiene su propio lock: lo que pasa acá no despierta a la música."""

    def __init__(self, ruta, max_por_persona=3):
        self.ruta = Path(ruta)
        self.max_por_persona = max_por_persona
        self.lock = threading.Lock()
        self.cambio = threading.Condition(self.lock)   # despierta a las páginas que esperan cambios
        self.salas = {}
        # Las versiones empiezan de cero en cada arranque: con esto las páginas se dan cuenta.
        self.arranque = uuid.uuid4().hex[:8]
        self.cambios = self.guardados = 0   # lo que hay que guardar / lo que ya está en tv.json
        self.cambio_en = 0.0
        self.lock_archivo = threading.Lock()   # (escriben el hilo de _bucle y cerrar)
        self.cerrado = False
        self.envios = {}                    # client -> cuándo mandó sus últimos mensajes al chat
        self.ordenes = {}                   # client -> cuándo usó el control
        self._leer()

    def arrancar(self):
        threading.Thread(target=self._bucle, daemon=True, name="tele").start()

    # --- el archivo

    def _leer(self):
        try:
            datos = json.loads(self.ruta.read_text())["salas"]
            if not isinstance(datos, list):
                raise TypeError("«salas» no es una lista")
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"  ⚠ no pude leer {self.ruta} ({e!r}): arranco sin salas de tv", flush=True)
            return
        for d in datos:
            try:
                s = Tele(str(d["id"]), str(d["nombre"])[:30], pin=str(d["pin"]), sal=str(d["sal"]))
            except (KeyError, TypeError):
                continue
            if not s.id or s.id in self.salas or len(self.salas) >= MAX_TELES:
                continue
            videos = lambda k: [i for i in map(_item, d.get(k) or []) if i] if isinstance(d.get(k), list) else []
            s.queue = videos("queue")[:MAX_COLA]
            en_cola = {i["id"] for i in s.queue}
            duenos = d.get("owners") if isinstance(d.get("owners"), dict) else {}
            s.owners = {k: str(v)[:64] for k, v in duenos.items() if k in en_cola}
            s.now = _item(d.get("now"))
            if s.now:
                s.t = self._segundo(d.get("t"), s.now["duration"]) or 0.0
                s.pausada = bool(d.get("pausada"))
                s.pauso = _nombre(d.get("pauso")) if s.pausada else None
            elif s.queue:
                s.now = s.queue.pop(0)
                s.owners.pop(s.now["id"], None)
            self.salas[s.id] = s

    def _guardar(self):
        """Escribe tv.json si cambió algo (la cola, el video, la pausa, las salas)."""
        with self.lock_archivo:
            with self.lock:
                if self.cambios == self.guardados:
                    return
                hasta = self.cambios
                texto = json.dumps({"salas": [{
                    "id": s.id, "nombre": s.nombre, "pin": s.pin, "sal": s.sal, "now": s.now,
                    "t": round(self._pos(s), 1), "pausada": s.pausada, "pauso": s.pauso,
                    "queue": s.queue, "owners": s.owners} for s in self.salas.values()]}, ensure_ascii=False)
            try:
                self.ruta.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.ruta.with_suffix(".tmp")
                tmp.write_text(texto)
                os.replace(tmp, self.ruta)
            except OSError as e:
                print(f"  ⚠ no pude guardar {self.ruta}: {e}", flush=True)
                return
            with self.lock:
                self.guardados = hasta

    def cerrar(self):
        """Al apagar la rockola: que quede por dónde iba cada video."""
        with self.lock:
            self.cerrado = True
            self.cambios += 1
        self._guardar()

    # --- el reloj (todo esto, con el lock tomado)

    def _bump(self, sala, guardar=False):
        """Marca un cambio en `sala`. `guardar`: es de los que quedan en tv.json."""
        sala.version += 1
        if guardar:
            self.cambios += 1
            self.cambio_en = time.monotonic()
        self.cambio.notify_all()

    def _pos(self, sala):
        """Por dónde va el video, en segundos (sin pasarse del final)."""
        t = sala.t + (time.monotonic() - sala.at if sala.corre else 0)
        d = sala.now["duration"] if sala.now else 0
        return min(t, d + GRACIA) if d else t

    def _medir(self, sala, t):
        sala.t, sala.at = float(t), time.monotonic()

    def _andar(self, sala):
        """Que el reloj corra o no, según corresponda. Devuelve si cambió."""
        if not sala.viendo:
            sala.lista = False   # cuando vuelva alguien, arranca con el primero que lo esté viendo
        debe = bool(sala.now and sala.lista and not sala.pausada)
        if debe == sala.corre:
            return False
        self._medir(sala, self._pos(sala))
        sala.corre = debe
        return True

    def _poner(self, sala, item):
        """Que vaya `item` (o nada), desde el principio."""
        sala.now = item
        sala.t, sala.at, sala.corre = 0.0, time.monotonic(), False
        sala.pausada, sala.pauso, sala.lista = False, None, False

    def _avanzar(self, sala):
        sig = sala.queue.pop(0) if sala.queue else None
        if sig:
            sala.owners.pop(sig["id"], None)
        self._poner(sala, sig)

    def _aviso(self, sala, texto, client="", error=False):
        """Algo para avisarles a los de la sala (`client`: quién lo hizo; a él no hace falta)."""
        n = (sala.notice or {}).get("id", 0) + 1
        sala.notice = {"id": n, "text": texto, "err": error, "client": client}

    @staticmethod
    def _segundo(valor, d):
        """El segundo que dice una página, si sirve (un número, dentro del video); si no, None."""
        if isinstance(valor, bool):
            return None
        try:
            t = float(valor)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(t) or t < 0:
            return None
        return min(t, d) if d else t

    def _mover(self, sala, ahora):
        """Cada segundo: da por cerradas las páginas de las que no se sabe nada, frena el reloj si
        ya no mira nadie y pasa al video que sigue cuando el reloj llega al final."""
        vencidas = [c for c, t in sala.viendo.items() if ahora - t >= VENCE]
        for c in vencidas:
            del sala.viendo[c]
        cambio = self._andar(sala) or bool(vencidas)
        now = sala.now
        if now and sala.corre and now["duration"] and self._pos(sala) >= now["duration"] + GRACIA:
            self._avanzar(sala)
            self._bump(sala, guardar=True)
        elif cambio:
            self._bump(sala)

    def _bucle(self, paso=1, junta=2):
        while not self.cerrado:
            time.sleep(paso)
            try:
                with self.lock:
                    ahora = time.monotonic()
                    for sala in list(self.salas.values()):
                        self._mover(sala, ahora)
                    # (un ratito después del último cambio: así se juntan los que vienen seguidos)
                    guardar = self.cambios != self.guardados and ahora - self.cambio_en >= junta
                if guardar:
                    self._guardar()
            except Exception as e:   # no debería pasar: que no se muera el hilo
                print(f"  ⚠ salas de tv: {e!r}", flush=True)

    def _frenar(self, client):
        """Que una página (o alguien a mano) no vuelva loca a la sala con el control."""
        ahora = time.monotonic()
        recientes = [t for t in self.ordenes.get(client, []) if ahora - t < 10]
        if len(recientes) >= MAX_ORDENES:
            raise ErrorTele(429, "Más despacio con el control: esperá unos segundos")
        if len(self.ordenes) > 1000:
            self.ordenes.clear()
        self.ordenes[client] = recientes + [ahora]

    def _vive(self, sala):
        if self.salas.get(sala.id) is not sala:
            raise ErrorTele(404, "Esa sala de tv ya no existe")

    # --- las salas

    def sala(self, sid):
        with self.lock:
            s = self.salas.get(str(sid or ""))
        if s is None:
            raise ErrorTele(404, "Esa sala de tv ya no existe")
        return s

    def publicas(self):
        with self.lock:
            return [s.publica() for s in self.salas.values()]

    def crear(self, base, nombre, pin, sal):
        """Una sala nueva. `base`: de dónde sale su id (el del link); `pin`: ya con sal y hash."""
        with self.lock:
            if any(s.nombre.casefold() == nombre.casefold() for s in self.salas.values()):
                raise ErrorTele(409, f"Ya hay una sala de tv «{nombre}»")
            if len(self.salas) >= MAX_TELES:
                raise ErrorTele(429, f"Ya hay {MAX_TELES} salas de tv: borren alguna que no se use")
            sid, n = base, 1
            while sid in self.salas:
                n += 1
                sid = f"{base}-{n}"
            s = self.salas[sid] = Tele(sid, nombre, pin=pin, sal=sal)
            self._avisar()
        print(f"  📺 Sala de tv nueva: {nombre}", flush=True)
        return s.publica()

    def borrar(self, sala):
        with self.lock:
            if self.salas.get(sala.id) is not sala:
                return
            del self.salas[sala.id]
            self._bump(sala, guardar=True)   # las páginas que estaban en ella se enteran
            self._avisar()
        print(f"  📺 Sala de tv borrada: {sala.nombre}", flush=True)

    def _avisar(self):
        for s in self.salas.values():
            self._bump(s, guardar=True)

    def avisar(self):
        """Cambió algo de afuera que las páginas muestran (la lista de salas de música)."""
        with self.lock:
            for s in self.salas.values():
                self._bump(s)

    # --- el estado, para las páginas

    def estado(self, sala, client="", since=None, viendo=False):
        """El estado de la sala. Con `since`, espera (hasta 25 s) a que cambie esa versión.
        `viendo`: esa página tiene el video a la vista (el reloj corre mientras alguna lo tenga)."""
        with self.cambio:
            self._vive(sala)
            if viendo:
                self._ver(sala, client)
            if since is not None:
                hasta = time.monotonic() + 25
                while since == sala.version and self.salas.get(sala.id) is sala and time.monotonic() < hasta:
                    self.cambio.wait(timeout=hasta - time.monotonic())
            self._vive(sala)
            if viendo and client in sala.viendo:
                # Sigue mirando (para que no se venza). Si mientras esperaba dejó de mirar, no.
                sala.viendo[client] = time.monotonic()
            now = sala.now
            d = now["duration"] if now else 0
            aviso = sala.notice
            return {
                "version": sala.version,
                "boot": self.arranque,
                "room": sala.publica(),
                "tvs": [s.publica() for s in self.salas.values()],
                "now": now,
                "queue": [dict(i, mine=bool(client) and sala.owners.get(i["id"]) == client) for i in sala.queue],
                "progress": {"t": round(min(self._pos(sala), d) if d else self._pos(sala), 2), "d": d,
                             "playing": sala.corre},
                "paused": sala.pausada,
                "paused_by": sala.pauso,
                # Esperando a que la primera página avise que ya lo está viendo, para arrancar el reloj.
                "starting": bool(now and not sala.lista and not sala.pausada and sala.viendo),
                "waiting": bool(now and not sala.pausada and not sala.viendo),   # en espera: nadie mira
                "viewers": len(sala.viendo),
                "notice": aviso and {"id": aviso["id"], "text": aviso["text"], "err": aviso["err"],
                                     "mine": bool(client) and aviso["client"] == client},
                "chat": [{"id": m["id"], "by": m["by"], "text": m["text"], "at": m["at"],
                          "mine": bool(client) and m["client"] == client} for m in sala.chat[-50:]],
            }

    def _ver(self, sala, client):
        if not client or (client not in sala.viendo and len(sala.viendo) >= 200):
            return
        nuevo = client not in sala.viendo
        sala.viendo[client] = time.monotonic()
        if nuevo:
            self._andar(sala)
            self._bump(sala)

    def mirar(self, sala, client, on):
        """Una página empezó a mirar (tiene el video a la vista) o dejó (la cerraron)."""
        client = str(client or "")[:64]
        if not client:
            raise ErrorTele(400, "Falta el identificador; recargá la página")
        with self.lock:
            self._vive(sala)
            if on:
                self._ver(sala, client)
            elif sala.viendo.pop(client, None) is not None:
                self._andar(sala)
                self._bump(sala, guardar=True)   # (si era el último, queda por dónde iba)

    # --- la cola

    def agregar(self, sala, video, client, by):
        client = str(client or "")[:64]
        if not client:   # sin esto no hay límite por persona ni se sabe de quién es
            raise ErrorTele(400, "Falta el identificador; recargá la página")
        datos = _video(video)
        if datos is None:
            raise ErrorTele(400, "Video inválido")
        item = dict(datos, id=uuid.uuid4().hex[:10], by=_nombre(by))
        with self.lock:
            self._vive(sala)
            vid = item["videoId"]
            if (sala.now and sala.now["videoId"] == vid) or any(i["videoId"] == vid for i in sala.queue):
                raise ErrorTele(409, "Ese video ya está en la cola")
            if sum(sala.owners.get(i["id"]) == client for i in sala.queue) >= self.max_por_persona:
                raise ErrorTele(429, f"Ya hay {self.max_por_persona} videos tuyos en la cola. Esperá a que pase alguno.")
            if len(sala.queue) >= MAX_COLA:
                raise ErrorTele(429, "La cola está llena")
            sala.queue.append(item)
            sala.owners[item["id"]] = client
            if sala.now is None:
                self._avanzar(sala)   # no había nada: va ya
            self._bump(sala, guardar=True)
        return item

    def quitar(self, sala, item_id, client, admin):
        item_id, client = str(item_id or ""), str(client or "")
        with self.lock:
            self._vive(sala)
            if not admin and (not client or sala.owners.get(item_id) != client):
                raise ErrorTele(403, "Sólo podés quitar tus propios videos")
            sala.queue = [i for i in sala.queue if i["id"] != item_id]
            sala.owners.pop(item_id, None)
            self._bump(sala, guardar=True)

    def primero(self, sala, item_id):
        """Con el PIN: que ese sea el que sigue."""
        item_id = str(item_id or "")
        with self.lock:
            self._vive(sala)
            sala.queue.sort(key=lambda i: i["id"] != item_id)   # sort estable: sólo sube ese
            self._bump(sala, guardar=True)

    # --- el control (de todos)

    def siguiente(self, sala, item_id, client, by):
        """⏭: pasa al video que sigue. Si `item_id` ya no es el que va, no hace nada (así dos que
        lo tocan a la vez no saltean dos)."""
        client = str(client or "")[:64]
        with self.lock:
            self._vive(sala)
            self._frenar(client)
            now = sala.now
            if now is None or now["id"] != str(item_id or ""):
                return
            self._avanzar(sala)
            self._aviso(sala, f"⏭ {_nombre(by, 'Alguien')} saltó «{now['title']}»", client)
            self._bump(sala, guardar=True)

    def control(self, sala, body):
        """Lo que alguien hizo con el video (pausa, seguir, ir a otro segundo) o lo que avisa su
        página (ya lo está viendo, terminó, no se puede ver). `id`: el video del que habla (si ya
        va otro, no importa); `t`: por dónde va en esa página."""
        client = str(body.get("client", ""))[:64]
        by, accion = _nombre(body.get("by"), "Alguien"), str(body.get("action", ""))
        with self.lock:
            self._vive(sala)
            self._frenar(client)
            now = sala.now
            if now is None or now["id"] != str(body.get("id", "")):
                return
            d, pos = now["duration"], self._pos(sala)
            t = self._segundo(body.get("t"), d)
            if accion == "pause":
                if sala.pausada:
                    return
                sala.pausada, sala.pauso = True, by
                self._medir(sala, t if t is not None and abs(t - pos) <= CERCA else pos)
            elif accion == "play":
                if not sala.pausada and sala.lista:
                    return
                sala.pausada, sala.pauso, sala.lista = False, None, True
                self._medir(sala, pos if t is None else t)   # sigue desde donde está quien le dio
            elif accion == "ready":
                if sala.lista or sala.pausada:
                    return
                sala.lista = True
                self._medir(sala, t if t is not None and t <= pos + ADELANTE else pos)
            elif accion == "seek":
                if not d or t is None or abs(t - pos) < 1:
                    return
                self._medir(sala, t)
                self._aviso(sala, f"⏩ {by} fue a {_hora(t)}", client)
            elif accion == "ended":
                if d and pos < d - FINAL:
                    return   # el reloj dice que falta: habrá sido otra cosa (p. ej. una publicidad)
                self._avanzar(sala)
            elif accion == "error":
                motivo = NO_SE_VE.get(body.get("code"))
                if not motivo:
                    return
                self._avanzar(sala)
                self._aviso(sala, f"Salteado «{now['title']}»: {motivo}", error=True)
            else:
                raise ErrorTele(400, "El control no hace eso")
            self._andar(sala)
            self._bump(sala, guardar=True)

    # --- el chat

    def chatear(self, sala, body):
        client = str(body.get("client", ""))[:64]
        name = str(body.get("by", "")).strip()[:30]
        texto = " ".join(str(body.get("text", "")).split())[:300]
        if not client:
            raise ErrorTele(400, "Falta el identificador; recargá la página")
        if not name:
            raise ErrorTele(400, "Poné tu nombre arriba primero")
        if not texto:
            raise ErrorTele(400, "Escribí algo")
        ahora = time.monotonic()
        with self.lock:
            self._vive(sala)
            recientes = [t for t in self.envios.get(client, []) if ahora - t < 10]
            if len(recientes) >= 5:
                raise ErrorTele(429, "Más despacio: esperá unos segundos")
            if len(self.envios) > 1000:
                self.envios.clear()
            self.envios[client] = recientes + [ahora]
            n = sala.chat[-1]["id"] + 1 if sala.chat else 1
            sala.chat = (sala.chat + [{"id": n, "by": name, "text": texto, "at": time.time(),
                                       "client": client}])[-MAX_CHAT:]
            self._bump(sala)
