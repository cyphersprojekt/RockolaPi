"""Estadísticas de la rockola, para el recap (/recap): qué sonó, cuánto, quién lo pidió.

Cada vez que una canción deja de sonar (terminó, la saltearon con ⏭, ▶ ahora…) se anota en una
base SQLite, `estadisticas.db` en la carpeta de la rockola, así que sobrevive a los reinicios. Se
anota cuánto sonó de verdad (no lo que dura el tema), quién la pidió, de dónde vino (pedida, de
una playlist o 🔀 al azar) y en qué sala.

YouTube no dice de qué género es un tema: se le pregunta a la API de búsqueda de iTunes (pública,
sin cuenta ni clave) por el artista, de a uno y sin apuro, y queda anotado en la misma base. Con
eso se arman los géneros del recap y los de las guardadas (🎸 por género: ver `generos_de`).
"""
import json
import os
import queue
import re
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from reproductor import _artistas, _para_buscar, _primer_artista

CUENTA = 30            # segundos que tiene que sonar un tema para contar como escuchado (o el 90%,
                       # si es más corto): lo que se corta antes es "salteado"
ITUNES = "https://itunes.apple.com/search"
ITUNES_PAIS = "AR"     # la tienda de iTunes que se consulta: así los géneros vienen en castellano
PAUSA_GENEROS = 4      # segundos entre preguntas a iTunes (pide no pasar de unas 20 por minuto)
REINTENTAR = 300       # si iTunes no contesta, se vuelve a probar a los 5 minutos
REPREGUNTAR = 30 * 86400   # a un artista que iTunes no conocía, se le vuelve a preguntar al mes
MAX_PEDIDOS = 5000     # artistas de las guardadas esperando a que se le pregunte su género a iTunes
UA = {"User-Agent": "Rockola/2.0 (rockola de oficina)"}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS escuchas (
    id            INTEGER PRIMARY KEY,
    sala          TEXT NOT NULL,
    cuando        REAL NOT NULL,     -- cuándo dejó de sonar (segundos unix)
    video_id      TEXT NOT NULL,
    titulo        TEXT NOT NULL,
    artista       TEXT NOT NULL,     -- como viene de YouTube Music: «Bizarrap, Quevedo»
    clave_artista TEXT NOT NULL,     -- el primero, para comparar (ver _junto): su género
    album         TEXT NOT NULL,
    tapa          TEXT NOT NULL,
    duracion      INTEGER NOT NULL,  -- lo que dura el tema
    escuchado     REAL NOT NULL,     -- lo que sonó de verdad
    por           TEXT NOT NULL,     -- quién la pidió, con el nombre que puso
    clave_por     TEXT NOT NULL,     -- en minúsculas y sin acentos: «Juli» y «juli» son la misma
    origen        TEXT NOT NULL      -- pedido | playlist | azar
);
CREATE INDEX IF NOT EXISTS escuchas_sala ON escuchas (sala, cuando);
CREATE TABLE IF NOT EXISTS generos (
    clave_artista TEXT PRIMARY KEY,
    genero        TEXT NOT NULL,     -- '' = iTunes no lo conoce
    consultado    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reacciones (
    id       INTEGER PRIMARY KEY,
    sala     TEXT NOT NULL,
    cuando   REAL NOT NULL,     -- cuándo dejó de sonar la canción (segundos unix)
    video_id TEXT NOT NULL,
    titulo   TEXT NOT NULL,
    artista  TEXT NOT NULL,
    tapa     TEXT NOT NULL,
    emoji    TEXT NOT NULL,     -- 🔥, ❤️ o 😴
    cuantas  INTEGER NOT NULL   -- cuántas personas reaccionaron así esa vez que sonó
);
CREATE INDEX IF NOT EXISTS reacciones_sala ON reacciones (sala, cuando);
PRAGMA user_version = 1;
"""

# Cuenta como escuchada (para los rankings) si sonó 30 s, o casi entera si es más corta.
VALE = f"escuchado >= MIN({CUENTA}, duracion * 0.9)"


def _junto(texto):
    """Para comparar nombres de artistas: «Charly García» y «CHARLY GARCIA» son el mismo."""
    return re.sub(r"[^a-z0-9]+", "", _para_buscar(texto))


def _clave_por(nombre):
    return " ".join(_para_buscar(nombre).split())


def rango(periodo, ahora=None):
    """El período del recap, como (desde, hasta) en segundos unix (None: sin límite), en la hora
    de esta máquina: "hoy", "7d", "30d", un año ("2026") o "todo". Si no es ninguno, ValueError."""
    ahora = ahora or datetime.now()
    p = str(periodo or "").strip()
    if p == "hoy":
        return ahora.replace(hour=0, minute=0, second=0, microsecond=0).timestamp(), None
    if p in ("7d", "30d"):
        return (ahora - timedelta(days=int(p[:-1]))).timestamp(), None
    if re.fullmatch(r"\d{4}", p) and 2000 <= int(p) < 3000:
        return datetime(int(p), 1, 1).timestamp(), datetime(int(p) + 1, 1, 1).timestamp()
    if p == "todo":
        return None, None
    raise ValueError(f"período desconocido: {periodo!r}")


def genero_de(artista):
    """El género de un artista según iTunes ("" si no lo conoce). Si no responde, tira la excepción.

    Sólo si el nombre coincide (sin fijarse en mayúsculas, acentos ni signos): un video subido
    por cualquiera trae de "artista" el canal, y no hay que adivinarle un género."""
    buscado = _junto(artista)
    if not buscado:
        return ""
    pedido = urllib.request.Request(f"{ITUNES}?" + urlencode(
        {"term": artista, "entity": "musicArtist", "limit": 5, "country": ITUNES_PAIS}), headers=UA)
    with urllib.request.urlopen(pedido, timeout=10) as r:
        resultados = (json.loads(r.read()) or {}).get("results") or []
    for x in resultados:
        if isinstance(x, dict) and _junto(x.get("artistName")) == buscado and x.get("primaryGenreName"):
            return str(x["primaryGenreName"]).strip()[:60]
    return ""


def _sumar_artistas(filas):
    """Las escuchas agrupadas por el texto del artista, [(artista, tapa, _, veces, segundos)], ->
    por artista, de más a menos escuchado. Un tema de varios («Bizarrap, Quevedo») cuenta para
    cada uno. La tapa es la del tema más escuchado de cada artista."""
    out = {}
    for artista, tapa, _, veces, segundos in filas:
        for a in _artistas(artista):
            d = out.setdefault(_junto(a) or a, {"name": a, "thumb": "", "plays": 0, "seconds": 0, "_tapa": 0})
            d["plays"] += veces
            d["seconds"] += segundos
            if tapa and veces > d["_tapa"]:
                d["thumb"], d["_tapa"] = tapa, veces
    orden = sorted(out.values(), key=lambda d: (-d["plays"], -d["seconds"], _para_buscar(d["name"])))
    for d in orden:
        del d["_tapa"]
        d["seconds"] = round(d["seconds"])
    return orden


class Estadisticas:
    """La base de las estadísticas. `anotar` no espera nada (lo escribe un hilo aparte): se
    puede llamar con el lock de la rockola tomado. `recap` arma todo lo que muestra la página.

    Si la base está rota (p. ej. se cortó la luz justo mientras se escribía), queda a un costado
    como estadisticas.rota-<fecha>.db y se empieza otra: la música no se tiene que frenar por esto.
    """

    def __init__(self, ruta, generos=True):
        self.ruta = Path(ruta)
        self.lock = threading.Lock()   # una sola conexión, para todos los hilos
        self.fila = queue.Queue()      # lo que falta escribir
        self.db = self._abrir()
        self._cerrando = threading.Event()
        self._artistas_nuevos = threading.Event()
        self._pedidos = {}             # clave -> artista: los que pidió generos_de y falta averiguar
        self._escritor = threading.Thread(target=self._bucle_escribir, daemon=True, name="estadisticas")
        self._escritor.start()
        if generos:
            self._artistas_nuevos.set()   # al arrancar, los que hayan quedado sin género
            threading.Thread(target=self._bucle_generos, daemon=True, name="generos").start()

    def _abrir(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        try:
            return self._conectar()
        except sqlite3.OperationalError:
            raise   # no se puede abrir (permisos, disco lleno…): no es que esté rota
        except sqlite3.DatabaseError as e:
            rota = self.ruta.with_name(f"{self.ruta.stem}.rota-{time.strftime('%Y%m%d-%H%M%S')}{self.ruta.suffix}")
            print(f"  ⚠ {self.ruta.name} está rota ({e}): la dejo como {rota.name} y empiezo otra", flush=True)
            for extra in ("", "-wal", "-shm"):
                try:
                    os.replace(f"{self.ruta}{extra}", f"{rota}{extra}")
                except FileNotFoundError:
                    pass
            return self._conectar()

    def _conectar(self):
        db = sqlite3.connect(self.ruta, check_same_thread=False, timeout=10)
        try:
            db.execute("PRAGMA journal_mode=WAL")    # aguanta mejor un corte de luz
            db.execute("PRAGMA synchronous=NORMAL")  # sin esperar a la tarjeta en cada canción
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("no pasó quick_check")
            db.executescript(ESQUEMA)
        except sqlite3.Error:
            db.close()
            raise
        return db

    def cerrar(self):
        """Escribe lo que quedaba en la fila y cierra la base."""
        self._cerrando.set()
        self._artistas_nuevos.set()
        self.fila.put(None)
        self._escritor.join(timeout=10)
        with self.lock:
            self.db.close()

    def al_dia(self):
        """Espera a que esté escrito todo lo anotado hasta ahora (para las pruebas)."""
        self.fila.join()

    # --- escribir

    def anotar(self, sala, item, escuchado):
        """`item` (una canción de la cola) sonó `escuchado` segundos en `sala`."""
        try:
            escuchado = float(escuchado or 0)
            duracion = int(item.get("duration") or 0)
        except (TypeError, ValueError, AttributeError):
            return
        if duracion:
            escuchado = min(escuchado, duracion)
        if escuchado < 1 or not item.get("videoId"):
            return   # no llegó a sonar (se estaba bajando, o falló)
        artista = str(item.get("artist") or "")[:200]
        por = " ".join(str(item.get("by") or "").split())[:30]
        origen = "azar" if item.get("azar") else "playlist" if item.get("playlist") else "pedido"
        self.fila.put(("anotar", (
            str(sala), time.time(), str(item["videoId"]), str(item.get("title") or "")[:200], artista,
            _junto(_primer_artista(artista)), str(item.get("album") or "")[:200], str(item.get("thumb") or "")[:500],
            duracion, round(escuchado, 1), por, _clave_por(por), origen)))

    def anotar_reacciones(self, sala, item, cuentas):
        """`item` terminó de sonar en `sala` con esas reacciones: {"🔥": 3, "❤️": 1}."""
        if not item.get("videoId"):
            return
        ahora = time.time()
        for emoji, n in cuentas.items():
            if isinstance(n, int) and n > 0:
                self.fila.put(("reaccion", (str(sala), ahora, str(item["videoId"]), str(item.get("title") or "")[:200],
                                            str(item.get("artist") or "")[:200], str(item.get("thumb") or "")[:500],
                                            str(emoji)[:8], n)))

    def borrar_sala(self, sala):
        """Al borrar una sala, se van sus estadísticas (si se crea otra con el mismo nombre, que
        no herede las de la anterior)."""
        self.fila.put(("borrar_sala", str(sala)))

    def _bucle_escribir(self):
        while True:
            tareas = [self.fila.get()]
            while True:   # las que se juntaron mientras tanto, de una vez
                try:
                    tareas.append(self.fila.get_nowait())
                except queue.Empty:
                    break
            nuevas = False
            try:
                with self.lock, self.db:
                    for t in tareas:
                        if t and t[0] == "anotar":
                            self.db.execute("""INSERT INTO escuchas (sala, cuando, video_id, titulo, artista,
                                clave_artista, album, tapa, duracion, escuchado, por, clave_por, origen)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", t[1])
                            nuevas = True
                        elif t and t[0] == "reaccion":
                            self.db.execute("""INSERT INTO reacciones (sala, cuando, video_id, titulo, artista, tapa,
                                emoji, cuantas) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", t[1])
                        elif t and t[0] == "borrar_sala":
                            self.db.execute("DELETE FROM escuchas WHERE sala = ?", (t[1],))
                            self.db.execute("DELETE FROM reacciones WHERE sala = ?", (t[1],))
            except sqlite3.Error as e:
                print(f"  ⚠ no pude anotar en las estadísticas: {e}", flush=True)
            if nuevas:
                self._artistas_nuevos.set()
            for _ in tareas:
                self.fila.task_done()
            if None in tareas:
                return

    # --- géneros

    def _bucle_generos(self):
        """Le pregunta a iTunes el género de los artistas que todavía no lo tienen (primero los
        más escuchados), de a uno. Si no contesta, prueba de nuevo en un rato."""
        avisado = False
        while not self._cerrando.is_set():
            self._artistas_nuevos.wait(timeout=6 * 3600)   # (de vez en cuando, los de REPREGUNTAR)
            self._artistas_nuevos.clear()
            try:
                faltan = self._sin_genero()
            except sqlite3.Error:
                continue
            for clave, artista in faltan:
                if self._cerrando.is_set():
                    return
                try:
                    genero = genero_de(artista)
                except Exception as e:   # sin internet, o iTunes pide que vayamos más despacio
                    if not avisado:
                        print(f"  ⚠ no pude averiguar géneros (pruebo más tarde): {e}", flush=True)
                        avisado = True
                    self._cerrando.wait(REINTENTAR)
                    self._artistas_nuevos.set()
                    break
                avisado = False
                try:
                    with self.lock, self.db:
                        self.db.execute("INSERT OR REPLACE INTO generos VALUES (?, ?, ?)", (clave, genero, time.time()))
                        self._pedidos.pop(clave, None)
                except sqlite3.Error:
                    return   # la cerraron
                self._cerrando.wait(PAUSA_GENEROS)
            else:
                if faltan:   # van de a 50: puede haber más
                    self._artistas_nuevos.set()

    def _sin_genero(self, cuantos=50):
        """[(clave, artista)] de los que falta averiguar el género: los más escuchados primero, y
        después los que pidió `generos_de` (los de las guardadas que todavía no sonaron)."""
        with self.lock:
            filas = self.db.execute("""
                SELECT e.clave_artista, e.artista, MAX(e.cuando) FROM escuchas e
                LEFT JOIN generos g USING (clave_artista)
                WHERE e.clave_artista != '' AND (g.genero IS NULL OR (g.genero = '' AND g.consultado < ?))
                GROUP BY e.clave_artista ORDER BY COUNT(*) DESC LIMIT ?""",
                (time.time() - REPREGUNTAR, cuantos)).fetchall()
            pedidos = list(self._pedidos.items())
        faltan = [(clave, _primer_artista(artista)) for clave, artista, _ in filas]
        ya = {clave for clave, _ in faltan}
        return faltan + [p for p in pedidos if p[0] not in ya][:cuantos - len(faltan)]

    def generos_de(self, artistas):
        """El género de cada uno de `artistas` (como vienen en un tema: «Bizarrap, Quevedo»; vale
        el del primero), para agrupar las guardadas: ({artista: género}, cuántos falta averiguar).
        En el dict están sólo los que se sabe. Los que nunca se le preguntaron a iTunes quedan
        anotados para preguntárselos, aunque no hayan sonado nunca (lo demás de las estadísticas
        sólo sabe de lo que sonó)."""
        claves = {a: _junto(_primer_artista(a)) for a in set(artistas)}
        viejo = time.time() - REPREGUNTAR
        with self.lock:
            sabidos = {c: (g, cuando) for c, g, cuando in
                       self.db.execute("SELECT clave_artista, genero, consultado FROM generos")}
            # Falta: no se preguntó nunca, o iTunes no lo conocía y ya pasó un mes.
            faltan = {c: _primer_artista(a) for a, c in claves.items()
                      if c and (c not in sabidos or (not sabidos[c][0] and sabidos[c][1] < viejo))}
            nuevos = faltan.keys() - self._pedidos.keys()
            if len(self._pedidos) + len(nuevos) > MAX_PEDIDOS:
                self._pedidos.clear()
            self._pedidos.update(faltan)
        if nuevos:
            self._artistas_nuevos.set()
        return ({a: sabidos[c][0] for a, c in claves.items() if c in sabidos and sabidos[c][0]},
                sum(c not in sabidos for c in faltan))

    # --- leer

    def recap(self, salas, desde=None, hasta=None, persona=None, top=10):
        """Todo lo del recap de `salas` (sus ids) entre `desde` y `hasta` (segundos unix; None: sin
        límite). Con `persona` (un nombre), sólo lo que pidió esa persona: sus pedidos y las
        playlists que cargó. Los rankings cuentan las que sonaron al menos 30 s (ver VALE); el
        tiempo, todo lo que sonó."""
        if not salas:
            salas = [""]
        cond, args = [f"sala IN ({', '.join('?' * len(salas))})"], list(salas)
        if persona:
            cond.append("clave_por = ? AND origen != 'azar'")
            args.append(_clave_por(persona))
        de_siempre = " AND ".join(cond), list(args)   # (sin el período: para saber qué años hay)
        if desde is not None:
            cond.append("cuando >= ?")
            args.append(desde)
        if hasta is not None:
            cond.append("cuando < ?")
            args.append(hasta)
        donde = " AND ".join(cond)
        vale = f"{donde} AND {VALE}"
        # A qué hora (y qué día) empezó a sonar, en la hora de esta máquina.
        local = lambda fmt: f"strftime('{fmt}', cuando - escuchado, 'unixepoch', 'localtime')"

        with self.lock:
            q = lambda sql, *extra: self.db.execute(sql, [*args, *extra]).fetchall()
            total, segundos, distintas, pedidos, de_playlist, al_azar, personas = q(f"""
                SELECT COUNT(*), COALESCE(SUM(escuchado), 0), COUNT(DISTINCT video_id),
                       COALESCE(SUM(origen = 'pedido'), 0), COALESCE(SUM(origen = 'playlist'), 0),
                       COALESCE(SUM(origen = 'azar'), 0),
                       COUNT(DISTINCT CASE WHEN origen = 'pedido' THEN clave_por END)
                FROM escuchas WHERE {donde}""")[0]
            escuchadas = q(f"SELECT COUNT(*) FROM escuchas WHERE {vale}")[0][0]
            canciones = q(f"""
                SELECT video_id, titulo, artista, tapa, MAX(cuando), COUNT(*), SUM(escuchado)
                FROM escuchas WHERE {vale} GROUP BY video_id ORDER BY 6 DESC, 7 DESC LIMIT ?""", top)
            por_artista = q(f"""
                SELECT artista, tapa, MAX(cuando), COUNT(*), SUM(escuchado)
                FROM escuchas WHERE {vale} GROUP BY artista, tapa""")
            generos = q(f"""
                SELECT COALESCE(g.genero, ''), COUNT(*), SUM(escuchado)
                FROM escuchas LEFT JOIN generos g USING (clave_artista)
                WHERE {vale} GROUP BY 1 ORDER BY 2 DESC, 3 DESC""")
            quienes = [] if persona else q(f"""
                SELECT por, MAX(cuando), clave_por, COUNT(*), SUM(escuchado)
                FROM escuchas WHERE {donde} AND origen = 'pedido' AND clave_por != ''
                GROUP BY clave_por ORDER BY 4 DESC, 5 DESC LIMIT ?""", top)
            gustos = [] if persona else q(f"""
                SELECT clave_por, artista, '', 0, COUNT(*), SUM(escuchado)
                FROM escuchas WHERE {donde} AND origen = 'pedido' GROUP BY clave_por, artista""")
            horas = q(f"SELECT CAST({local('%H')} AS INTEGER), SUM(escuchado) FROM escuchas WHERE {donde} GROUP BY 1")
            dias = q(f"SELECT CAST({local('%w')} AS INTEGER), SUM(escuchado) FROM escuchas WHERE {donde} GROUP BY 1")
            record = q(f"""
                SELECT {local('%Y-%m-%d')}, SUM(escuchado), COUNT(*) FROM escuchas WHERE {donde}
                GROUP BY 1 ORDER BY 2 DESC LIMIT 1""")
            salteada = q(f"""
                SELECT video_id, titulo, artista, tapa, MAX(cuando), COUNT(*)
                FROM escuchas WHERE {donde} AND NOT ({VALE})
                GROUP BY video_id HAVING COUNT(*) >= 2 ORDER BY 6 DESC LIMIT 1""")
            # Las reacciones (🔥 ❤️ 😴) son de todos: con `persona` no se muestran. De cada una, la
            # canción que más juntó en el período (la primera de cada emoji, en este orden).
            reacciones = [] if persona else self.db.execute(f"""
                SELECT emoji, video_id, titulo, artista, tapa, SUM(cuantas), MAX(cuando)
                FROM reacciones WHERE sala IN ({', '.join('?' * len(salas))})
                    AND cuando >= ? AND cuando < ?
                GROUP BY emoji, video_id ORDER BY 6 DESC, 7 DESC""",
                [*salas, desde if desde is not None else 0, hasta if hasta is not None else 1e18]).fetchall()
            anios = [a for (a,) in self.db.execute(f"""
                SELECT DISTINCT strftime('%Y', cuando, 'unixepoch', 'localtime') FROM escuchas
                WHERE {de_siempre[0]} ORDER BY 1 DESC""", de_siempre[1])]
            # Cada persona, con el nombre como más lo escribió («Juli», no «juli» porque una vez se
            # le pasó): al ordenar así, el que queda en el dict es el más usado (y entre esos, el último).
            nombres = dict(self.db.execute(f"""
                SELECT clave_por, por FROM escuchas WHERE {de_siempre[0]} AND origen != 'azar'
                GROUP BY clave_por, por ORDER BY COUNT(*), MAX(cuando)""", de_siempre[1]).fetchall())

        artistas = _sumar_artistas(por_artista)
        favoritos = {}
        for clave, *resto in gustos:
            favoritos.setdefault(clave, []).append(resto)
        sin_genero = sum(n for g, n, _ in generos if not g)
        cancion = lambda f: {"videoId": f[0], "title": f[1], "artist": f[2], "thumb": f[3]}
        semana = dict(dias)
        mas_reacciones = {}
        for emoji, *resto in reacciones:
            mas_reacciones.setdefault(emoji, resto)
        return {
            "person": (nombres.get(_clave_por(persona)) or " ".join(str(persona).split())) if persona else None,
            "years": anios,
            "totals": {"plays": escuchadas, "all": total, "seconds": round(segundos), "songs": distintas,
                       "artists": len(artistas), "requests": pedidos, "playlist": de_playlist,
                       "shuffle": al_azar, "people": personas},
            "songs": [dict(cancion(f), plays=f[5], seconds=round(f[6])) for f in canciones],
            "artists": artistas[:top],
            "genres": [{"name": g, "plays": n, "seconds": round(s)} for g, n, s in generos if g],
            "unknownGenre": sin_genero,
            "people": [{"name": nombres.get(clave) or por, "requests": n, "seconds": round(s),
                        "artist": next((a["name"] for a in _sumar_artistas(favoritos.get(clave, []))), "")}
                       for por, _, clave, n, s in quienes],
            "hours": [round(dict(horas).get(h, 0)) for h in range(24)],
            "weekdays": [round(semana.get(d % 7, 0)) for d in range(1, 8)],   # de lunes a domingo
            "topDay": {"date": record[0][0], "seconds": round(record[0][1]), "plays": record[0][2]} if record else None,
            "skipped": dict(cancion(salteada[0]), times=salteada[0][5]) if salteada else None,
            # la que más juntó de cada reacción: [{"emoji": "🔥", videoId, title, artist, thumb, "count": 7}]
            "reactions": [dict(cancion(f), emoji=emoji, count=f[4]) for emoji, f in mas_reacciones.items()],
        }
