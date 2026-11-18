#!/usr/bin/env python3
"""Rockola · pantalla de terminal.

Muestra qué suena, la cola y un QR para pedir temas. Habla con el servidor por
HTTP, así que se puede abrir en la misma máquina o por SSH.

    python tui.py                              # servidor en http://localhost:8000
    python tui.py --url http://otra-pc:8000    # sólo mirar (los controles andan desde localhost)
    python tui.py --ciclo 0                    # sin ir y venir a las estadísticas

Cada tantos segundos (--ciclo) cambia sola entre el reproductor y las estadísticas (lo más
escuchado de hoy, de la semana y del año, por turno). El QR y lo que suena están siempre.

Teclas: espacio pausa · n siguiente · a anterior · ↑↓ elegir en la cola · t subir · d quitar · + - volumen
        m mezclar la cola · z aleatorio (con la cola vacía, suenan canciones guardadas al azar)
        e estadísticas (ir y volver) · q salir
"""
import argparse
import io
import json
import threading
import time
import urllib.error
import urllib.request

from rich.color import Color
from rich.console import Group
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Static

try:
    from PIL import Image, ImageOps
except ImportError:  # sin Pillow no hay tapa del disco, el resto anda igual
    Image = None
try:
    import qrcode
except ImportError:
    qrcode = None


# ------------------------------------------------------------------ helpers

class ErrorApi(Exception):
    pass


def pedir(base, path, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except Exception:
            msg = None
        raise ErrorApi(msg or f"Error {e.code}")


def fmt(s):
    s = max(0, int(s or 0))
    h, m = divmod(s // 60, 60)
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def cuanto(s):
    """Segundos -> «27 h 57 min», «45 min», «30 s» (como en el recap de la página)."""
    s = round(s or 0)
    if s < 60:
        return f"{s} s"
    m = round(s / 60)
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h} h {r} min" if r and h < 100 else f"{h} h"


def cortar(texto, ancho):
    texto = texto or ""
    return texto if len(texto) <= ancho else texto[: max(0, ancho - 1)] + "…"


def bloques(pixeles, ancho, alto):
    """Imagen -> texto con medios bloques: cada celda pinta 2 píxeles (arriba/abajo)."""
    t = Text(no_wrap=True)
    for y in range(0, alto - 1, 2):
        for x in range(ancho):
            arriba = pixeles[y * ancho + x]
            abajo = pixeles[(y + 1) * ancho + x]
            t.append("▀", Style(color=Color.from_rgb(*arriba), bgcolor=Color.from_rgb(*abajo)))
        if y < alto - 2:
            t.append("\n")
    return t


def qr_bloques(texto):
    """QR en medios bloques. Negro sobre blanco fijo (si no, algunos celulares no lo leen)."""
    if not qrcode:
        return None
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(texto)
    qr.make(fit=True)
    m = qr.get_matrix()
    if len(m) % 2:
        m.append([False] * len(m[0]))
    estilo = Style(color=Color.from_rgb(0, 0, 0), bgcolor=Color.from_rgb(255, 255, 255))
    t = Text(no_wrap=True)
    for y in range(0, len(m), 2):
        t.append("".join(" ▄▀█"[m[y][x] * 2 + m[y + 1][x]] for x in range(len(m[0]))), estilo)
        if y < len(m) - 2:
            t.append("\n")
    return t


def tapa_vacia(n):
    """Placeholder cuando no hay imagen: un degradé."""
    px = []
    for y in range(n):
        for x in range(n):
            k = (x + y) / (2 * n)
            px.append((int(60 + 120 * k), int(40 + 40 * k), int(90 + 80 * (1 - k))))
    return bloques(px, n, n)


# ---------------------------------------------------------------------- app

class Rockola(App):
    TITLE = "Rockola"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: $background; }
    #cabecera { height: 1; padding: 0 1; background: $panel; color: $text; }
    #arriba { height: auto; padding: 1 1 0 1; }
    #arte { width: auto; height: auto; }
    #info { width: 1fr; height: auto; padding: 0 2; }
    #qr { width: auto; height: auto; display: none; padding: 0 1; }
    #qr.visible { display: block; }
    #titulo-cola { height: 1; padding: 0 1; margin-top: 1; color: $text-muted; text-style: bold; }
    DataTable { height: 1fr; margin: 0 1; background: $background; }
    #recap { height: 1fr; margin: 1 1 0 1; display: none; }
    """
    BINDINGS = [
        Binding("space", "pausa", "Pausa"),
        Binding("n", "siguiente", "Siguiente"),
        Binding("a", "anterior", "Anterior"),
        Binding("t", "subir", "Subir"),
        Binding("d,delete", "quitar", "Quitar"),
        Binding("plus,equals_sign", "volumen(5)", "Vol +"),
        Binding("minus", "volumen(-5)", "Vol -"),
        Binding("z", "aleatorio", "Al azar"),
        Binding("m", "mezclar", "Mezclar"),
        Binding("e", "recap", "Stats"),
        Binding("q", "quit", "Salir"),
    ]
    # Las estadísticas van rotando: lo de hoy, lo de los últimos 7 días y lo del año.
    PERIODOS = ("hoy", "7d", "año")
    BARRA = 8   # celdas de la barrita de cada renglón de un ranking

    def __init__(self, base, tema, ciclo=15):
        super().__init__()
        self.base = base.rstrip("/")
        self.tema_inicial = tema
        self.ciclo = max(0, ciclo)    # segundos en cada vista (0: siempre el reproductor)
        self.vista = "reproductor"    # o "recap": las estadísticas
        self.cambia = time.monotonic() + self.ciclo
        self.recap = None             # las estadísticas que se están mostrando (lo de /api/stats)
        self.periodo = 0              # cuál de PERIODOS toca la próxima vez
        self.sonando = None           # el id de lo que suena (cuando cambia, se vuelve al reproductor)
        self.reaccion_vista = None    # el número de la última reacción que llegó (None: todavía ninguna)
        self.reaccion = None          # (hasta cuándo, emoji, quién): la que se está mostrando un instante
        self.estado = None
        self.recibido = time.monotonic()
        self.url_publica = ""
        self.conectado = False
        self.ultimo_aviso = None
        self.tapas = {}            # url -> (pixeles, n)
        self.tapa_pedida = None
        self.firma_cola = None
        self.acento = "yellow"

    # --- armado

    def compose(self) -> ComposeResult:
        yield Static(id="cabecera")
        with Horizontal(id="arriba"):
            yield Static(id="arte")
            yield Static(id="info")
            yield Static(id="qr")
        yield Static("PRÓXIMAS", id="titulo-cola")
        yield DataTable(cursor_type="row", zebra_stripes=True, show_cursor=True)
        yield Static(id="recap")   # (en lugar de la cola, en la vista de las estadísticas)
        yield Footer()

    def on_mount(self):
        try:
            self.theme = self.tema_inicial
        except Exception:
            pass
        # Colores del tema para el texto de Rich (no entiende las variables $ de Textual).
        self.acento = self.get_css_variables().get("accent") or "yellow"
        self.pintar_cabecera()
        self.pintar_todo()
        threading.Thread(target=self.escuchar, daemon=True).start()
        threading.Thread(target=self.buscar_info, daemon=True).start()
        self.set_interval(0.5, self.pintar_info)
        self.set_interval(1, self.ciclar)

    def on_key(self, event):
        """Con el teclado en la mano no va y viene sola: cualquier tecla (menos «e», que es para
        cambiar de vista) vuelve al reproductor y lo deja quieto un minuto."""
        if event.key == "e":
            return
        if self.vista == "recap":
            self.mostrar("reproductor")
        self.cambia = time.monotonic() + max(60, self.ciclo)

    def on_resize(self, _):
        self.firma_cola = None
        self.call_after_refresh(self.pintar_todo)   # (después: acá `self.size` todavía es el de antes)

    # --- hilos que hablan con el servidor

    def buscar_info(self):
        """Link para pedir temas. Se refresca: en una Raspberry el wifi puede conectarse tarde."""
        while self.is_running:
            try:
                url = pedir(self.base, "/api/info")["url"]
                if url != self.url_publica:
                    self.url_publica = url
                    self.call_from_thread(self.pintar_todo)
                time.sleep(30)
            except Exception:
                time.sleep(2)

    def escuchar(self):
        """Long-poll: el servidor contesta apenas cambia algo."""
        version = None
        while self.is_running:
            try:
                path = "/api/state" + (f"?since={version}" if version is not None else "")
                s = pedir(self.base, path, timeout=40)
                version = s["version"]
                self.call_from_thread(self.aplicar, s)
            except Exception:
                version = None
                try:
                    self.call_from_thread(self.sin_conexion)
                except Exception:
                    return  # la app ya se cerró
                time.sleep(2)

    def bajar_tapa(self, url, n):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                img = Image.open(io.BytesIO(r.read())).convert("RGB")
            img = ImageOps.fit(img, (n, n), Image.LANCZOS)  # recorte cuadrado centrado
            px = list(img.getdata())
            self.tapas[(url, n)] = px
            self.call_from_thread(self.pintar_arte)
        except Exception:
            self.tapas[(url, n)] = None

    def accion(self, path, body=None):
        def correr():
            try:
                pedir(self.base, path, body or {})
            except ErrorApi as e:
                self.call_from_thread(self.notify, str(e), severity="error", markup=False)
            except Exception:
                self.call_from_thread(self.notify, "No hay conexión con la rockola", severity="error")
        threading.Thread(target=correr, daemon=True).start()

    # --- estado

    def aplicar(self, s):
        self.estado = s
        self.recibido = time.monotonic()
        self.conectado = True
        aviso = s.get("notice")
        if aviso and self.ultimo_aviso is not None and aviso["id"] != self.ultimo_aviso:
            self.notify(aviso["text"], severity="warning", timeout=8, markup=False)
        self.ultimo_aviso = aviso["id"] if aviso else 0
        ultima = (s.get("reactions") or {}).get("last")
        if ultima and self.reaccion_vista is not None and ultima["n"] != self.reaccion_vista:
            self.reaccion = (time.monotonic() + 6, ultima["emoji"], ultima["by"])   # nueva: unos segundos a la vista
        self.reaccion_vista = ultima["n"] if ultima else 0
        ahora = (s.get("now") or {}).get("id")
        if self.vista == "recap" and ahora != self.sonando:
            self.mostrar("reproductor")   # empezó otro tema: que se vea en grande
        else:
            self.pintar_todo()
        self.sonando = ahora

    def sin_conexion(self):
        self.conectado = False
        if self.vista == "recap":
            self.mostrar("reproductor")
        self.pintar_cabecera()

    # --- las dos vistas: el reproductor (lo de siempre) y las estadísticas

    def ciclar(self):
        """Cada `ciclo` segundos cambia de vista. A las estadísticas pasa recién cuando llegan,
        y sólo si hay algo para mostrar."""
        if not self.ciclo or not self.conectado or time.monotonic() < self.cambia:
            return
        if self.vista == "recap":
            self.mostrar("reproductor")
        else:
            self.cambia = time.monotonic() + self.ciclo   # (si no hay nada, se prueba en la próxima vuelta)
            threading.Thread(target=self.traer_recap, daemon=True).start()

    def traer_recap(self, avisar=False):
        """Trae las estadísticas del próximo período que tenga algo y las muestra."""
        for _ in self.PERIODOS:
            periodo = self.PERIODOS[self.periodo % len(self.PERIODOS)]
            self.periodo += 1
            try:
                d = pedir(self.base, "/api/stats?periodo=" + (time.strftime("%Y") if periodo == "año" else periodo))
                hay = d["totals"]["plays"] > 0
            except Exception:   # sin estadísticas, o sin conexión: sigue el reproductor
                break
            if hay:
                try:
                    self.call_from_thread(self.mostrar, "recap", d)
                except Exception:
                    pass   # la app ya se cerró
                return
        if avisar:
            try:
                self.call_from_thread(self.notify, "Todavía no hay estadísticas para mostrar")
            except Exception:
                pass

    def mostrar(self, vista, recap=None):
        self.vista, self.cambia = vista, time.monotonic() + self.ciclo
        if recap:
            self.recap = recap
        en_recap = vista == "recap"
        self.query_one("#arte").display = not en_recap
        self.query_one("#titulo-cola").display = not en_recap
        self.query_one(DataTable).display = not en_recap
        self.query_one("#recap").display = en_recap
        self.pintar_todo()

    # --- dibujo

    def lado_tapa(self):
        return 32 if self.size.height >= 38 else 24 if self.size.height >= 26 else 16

    def pintar_todo(self):
        self.pintar_cabecera()
        self.pintar_arte()
        self.pintar_info()
        self.pintar_qr_lateral()
        self.pintar_cola()
        self.pintar_recap()

    def pintar_cabecera(self):
        t = Text(no_wrap=True)
        t.append(" ♫ ROCKOLA ", style="bold reverse")
        # El led: si la rockola tiene internet (verde), si no (rojo: sigue sonando lo guardado) o
        # si no se sabe (apagado: todavía no lo miró, o no hay conexión con ella).
        online = (self.estado or {}).get("online") if self.conectado else None
        t.append(" ●", style="bold green" if online else "bold red" if online is False else "dim")
        if online is False:
            t.append(" sin internet", style="bold red")
        if not self.conectado:
            t.append(f"  ⚠ sin conexión con {self.base} · reintentando…", style="bold red")
        elif self.url_publica:
            t.append("  pedí tu tema en ", style="dim")
            t.append(self.url_publica, style="bold underline")
        if self.estado and self.estado.get("mode") == "navegador":
            t.append("   (modo navegador: pausa y volumen desde /player)", style="dim")
        elif self.estado and self.estado.get("shuffle"):
            t.append("   🔀 al azar", style=f"bold {self.acento}")
        # A la derecha, el clima (lo trae la rockola): «☁ 18° nublado», o lo que entre.
        w = (self.estado or {}).get("weather") if self.conectado else None
        if w:
            for clima in (Text.assemble((f"{w['symbol']} {w['temp']}°", "bold"), (f" {w['text']}", "dim")),
                          Text(f"{w['symbol']} {w['temp']}°", style="bold")):
                libre = self.size.width - 2 - t.cell_len - clima.cell_len
                if libre >= 2:
                    t.append(" " * libre)
                    t.append_text(clima)
                    break
        self.query_one("#cabecera", Static).update(t)

    def pintar_arte(self):
        arte = self.query_one("#arte", Static)
        now = (self.estado or {}).get("now")
        if not now:  # sin música: QR grande para que pidan
            qr = qr_bloques(self.url_publica) if self.url_publica else None
            arte.update(qr or tapa_vacia(self.lado_tapa()))
            return
        n = self.lado_tapa()
        url = now.get("thumb")
        if Image and url:
            clave = (url, n)
            if clave in self.tapas and self.tapas[clave]:
                arte.update(bloques(self.tapas[clave], n, n))
                return
            if clave not in self.tapas and self.tapa_pedida != clave:
                self.tapa_pedida = clave
                threading.Thread(target=self.bajar_tapa, args=(url, n), daemon=True).start()
        arte.update(tapa_vacia(n))

    def pintar_info(self):
        if self.vista == "recap":
            return self.pintar_resumen()
        info = self.query_one("#info", Static)
        s = self.estado
        t = Text()
        if not s:
            t.append("\nConectando con la rockola…", style="dim")
            info.update(t)
            return
        now, p = s.get("now"), s.get("progress") or {}
        if not now:
            t.append("\nNADA SONANDO\n\n", style="bold")
            t.append("Escaneá el QR o entrá a\n", style="dim")
            t.append(self.url_publica or "…", style="bold underline")
            t.append("\ny pedí un tema.", style="dim")
            info.update(t)
            return
        if s.get("loading"):
            estado, estilo = "⏳ CARGANDO…", "bold yellow"
        elif p.get("playing"):
            estado, estilo = "▶ SONANDO AHORA", "bold green"
        else:
            estado, estilo = "⏸ EN PAUSA", "bold yellow"
        ancho = max(10, info.size.width - 4) if info.size.width else 40
        t.append(estado + "\n\n", style=estilo)
        t.append(now["title"] + "\n", style="bold")
        t.append(now["artist"] + "\n\n", style="")
        if now.get("azar"):
            t.append("🔀 al azar, de las guardadas\n", style="bold")
        else:
            t.append("pedida por ", style="dim")
            t.append(now["by"] + "\n", style="bold")
        t.append_text(self.renglon_de_reacciones(s))
        t.append("\n\n")

        # progreso: interpolado entre mensajes del servidor
        d = p.get("d") or now.get("duration") or 0
        tt = (p.get("t") or 0) + ((time.monotonic() - self.recibido) if p.get("playing") else 0)
        tt = min(tt, d) if d else tt
        tiempos = f"  {fmt(tt)} / {fmt(d)}"
        largo = max(5, ancho - len(tiempos))
        lleno = int(largo * (tt / d)) if d else 0
        t.append("━" * lleno, style=f"bold {self.acento}")
        t.append("─" * (largo - lleno), style="dim")
        t.append(tiempos + "\n", style="")

        vol = s.get("volume")
        if vol is not None:
            n = 20
            v = min(n, round(n * vol / 130))
            t.append("\nvol ", style="dim")
            t.append("▮" * v, style=self.acento)
            t.append("▯" * (n - v), style="dim")
            t.append(f" {vol}%", style="dim")
        info.update(t)

    def renglon_de_reacciones(self, s):
        """Las reacciones a lo que suena (🔥 ♥ 😴, con cuántas van), los votos para saltearla y,
        por unos segundos, la última que llegó y de quién. Siempre el mismo renglón."""
        r, sk = s.get("reactions") or {}, s.get("skip")
        t = Text()
        # (el corazón, con un símbolo de una celda: el emoji con variante corre lo que sigue)
        for emoji, se_ve, estilo in (("🔥", "🔥", ""), ("❤️", "♥", "bold red"), ("😴", "😴", "")):
            n = (r.get("counts") or {}).get(emoji, 0)
            t.append(se_ve, style=estilo)
            t.append(f" {n}   ", style="bold" if n else "dim")
        if sk:   # (con palabras: en la pared, «1/3» solo no dice nada)
            t.append(f"saltear {sk['votes']}/{sk['needed']}", style="bold" if sk["votes"] else "dim")
        if self.reaccion and time.monotonic() < self.reaccion[0]:
            _, emoji, quien = self.reaccion
            t.append("   ← ", style="dim")
            t.append("♥" if emoji == "❤️" else emoji, style="bold red" if emoji == "❤️" else "")
            t.append(f" {quien}", style=f"bold {self.acento}")
        return t

    def pintar_resumen(self):
        """Arriba, en la vista de las estadísticas: cuánto sonó en el período y, en chiquito,
        lo que suena ahora (con su barra, que sigue avanzando)."""
        d, s = self.recap, self.estado or {}
        tot = d["totals"]
        t = Text()
        cuando = {"hoy": "HOY", "7d": "EN LOS ÚLTIMOS 7 DÍAS"}.get(d.get("period"), f"EN {d.get('period')}")
        t.append(f"📊 LO QUE SONÓ {cuando}\n\n", style=f"bold {self.acento}")
        t.append(cuanto(tot["seconds"]), style="bold")
        t.append(" de música\n")
        contar = lambda n, uno, varios: f"{n} {uno if n == 1 else varios}"
        t.append(" · ".join([contar(tot["plays"], "canción", "canciones"), contar(tot["songs"], "distinta", "distintas"),
                             contar(tot["artists"], "artista", "artistas")]) + "\n")
        if tot["requests"]:
            t.append(f"{contar(tot['requests'], 'pedido', 'pedidos')} de {contar(tot['people'], 'persona', 'personas')}\n")
        pico = max(range(24), key=lambda h: d["hours"][h])
        if d["hours"][pico]:
            t.append(f"la hora pico: de {pico} a {pico + 1} h\n", style="dim")

        now, p = s.get("now"), s.get("progress") or {}
        t.append("\n")
        if not now:
            t.append("Nada sonando: escaneá el QR y pedí un tema.", style="dim")
        else:
            estado, estilo = ("⏳", "bold yellow") if s.get("loading") else ("▶", "bold green") if p.get("playing") else ("⏸", "bold yellow")
            t.append(f"{estado} ", style=estilo)
            t.append(now["title"], style="bold")
            t.append(f" · {now['artist']}\n")
            dur = p.get("d") or now.get("duration") or 0
            tt = (p.get("t") or 0) + ((time.monotonic() - self.recibido) if p.get("playing") else 0)
            tt = min(tt, dur) if dur else tt
            largo = 24
            lleno = int(largo * (tt / dur)) if dur else 0
            t.append("  " + "━" * lleno, style=f"bold {self.acento}")
            t.append("─" * (largo - lleno), style="dim")
            t.append(f"  {fmt(tt)} / {fmt(dur)}")
            t.append("  🔀 al azar" if now.get("azar") else f"  pedida por {now['by']}", style="dim")
        self.query_one("#info", Static).update(t)

    def pintar_recap(self):
        """Abajo, en la vista de las estadísticas: los rankings, uno al lado del otro."""
        if self.vista != "recap" or not self.recap:
            return
        d = self.recap

        def ranking(titulo, unidad, filas):
            """Un ranking: el puesto, el nombre (y de quién es), una barrita y el número."""
            tabla = Table.grid(padding=(0, 1), expand=True)
            tabla.add_column(justify="right", width=2, style="dim")
            tabla.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
            tabla.add_column(width=self.BARRA, no_wrap=True)
            tabla.add_column(justify="right", no_wrap=True, style="dim")
            tope = max((n for *_, n in filas), default=0) or 1
            for i, (nombre, de, n) in enumerate(filas, 1):
                lleno = max(1, round(self.BARRA * n / tope))
                barra = Text("━" * lleno, style=self.acento)
                barra.append("─" * (self.BARRA - lleno), style="dim")
                # (Text y no str: un compañero llamado "[/]" rompería la pantalla)
                tabla.add_row(Text(str(i)), Text.assemble((nombre, "bold" if i == 1 else ""), (f" · {de}" if de else "", "dim")),
                              barra, Text(str(n)))
            return Group(Text.assemble((titulo, "bold"), (f" · {unidad}", "dim")), tabla)

        columnas = [ranking("TOP CANCIONES", "veces", [(c["title"], c["artist"], c["plays"]) for c in d["songs"]]),
                    ranking("TOP ARTISTAS", "veces", [(a["name"], "", a["plays"]) for a in d["artists"]])]
        gente = d["people"] and ranking("QUIÉN PIDE MÁS", "pedidos", [(q["name"], q.get("artist") or "", q["requests"]) for q in d["people"]])
        generos = d["genres"] and ranking("GÉNEROS", "veces", [(g["name"], "", g["plays"]) for g in d["genres"][:10]])
        # (los dos si entran; si no, quién pide más, y los géneros sólo si nadie pidió nada)
        columnas += [c for c in ((gente, generos) if self.size.width >= 150 else (gente or generos,)) if c]
        fuera = Table.grid(padding=(0, 3), expand=True)
        for i, _ in enumerate(columnas):
            fuera.add_column(ratio=3 if i else 4)   # (las canciones, más anchas: llevan también el artista)
        fuera.add_row(*columnas)
        self.query_one("#recap", Static).update(fuera)

    def pintar_qr_lateral(self):
        panel = self.query_one("#qr", Static)
        ancho_ok = self.size.width >= 110
        hay_musica = bool((self.estado or {}).get("now"))
        # En las estadísticas el QR está siempre (ahí no hay tapa: no lo reemplaza el de la izquierda).
        se_ve = self.vista == "recap" or (ancho_ok and hay_musica)
        qr = qr_bloques(self.url_publica) if (self.url_publica and se_ve) else None
        if qr:
            t = Text("Pedí tu tema\n", style="bold", justify="center")
            t.append_text(qr)
            panel.update(t)
            panel.add_class("visible")
        else:
            panel.remove_class("visible")

    def pintar_cola(self):
        s = self.estado or {}
        cola = s.get("queue") or []
        total = sum(i.get("duration") or 0 for i in cola)
        titulo = f"PRÓXIMAS · {len(cola)} · {fmt(total)}" if cola else "PRÓXIMAS · la cola está vacía"
        self.query_one("#titulo-cola", Static).update(titulo)

        firma = (tuple((i["id"], i["by"], i.get("votes")) for i in cola), self.size.width)
        if firma == self.firma_cola:
            return
        self.firma_cola = firma
        tabla = self.query_one(DataTable)
        elegida = self.id_elegido()
        tabla.clear(columns=True)
        libre = max(20, self.size.width - 2 - 4 - 6 - 14 - 6 - 10)  # #, dur, pidió, votos y separaciones
        w_tit, w_art = int(libre * 0.6), libre - int(libre * 0.6)
        tabla.add_column("#", width=3)
        tabla.add_column("Canción", width=w_tit)
        tabla.add_column("Artista", width=w_art)
        tabla.add_column("Dur.", width=5)
        tabla.add_column("Pidió", width=12)
        tabla.add_column("Votos", width=5)   # los 👍 que le pusieron desde la web (la suben en la cola)
        ids = []
        for n, it in enumerate(cola, 1):
            # Text(...) y no str: DataTable interpreta los str como markup, y un
            # compañero llamado "[/]" rompería la pantalla.
            votos = it.get("votes") or 0
            tabla.add_row(Text(str(n)), Text(cortar(it["title"], w_tit)), Text(cortar(it["artist"], w_art)),
                          Text(fmt(it["duration"])), Text(cortar(it["by"], 12)),
                          Text(f"▲ {votos}" if votos else "", style=f"bold {self.acento}"), key=it["id"])
            ids.append(it["id"])
        if elegida in ids:
            tabla.move_cursor(row=ids.index(elegida))

    def id_elegido(self):
        tabla = self.query_one(DataTable)
        if not tabla.row_count:
            return None
        try:
            return tabla.coordinate_to_cell_key(tabla.cursor_coordinate).row_key.value
        except Exception:
            return None

    # --- acciones (teclas)

    def action_pausa(self):
        self.accion("/api/pause")

    def action_siguiente(self):
        now = (self.estado or {}).get("now")
        if now:
            self.accion("/api/next", {"finished": now["id"], "by": ""})   # (a mano: queda anotado en el chat)

    def action_anterior(self):
        self.accion("/api/prev")

    def action_subir(self):
        if (i := self.id_elegido()):
            self.accion("/api/first", {"id": i})

    def action_quitar(self):
        if (i := self.id_elegido()):
            self.accion("/api/remove", {"id": i})

    def action_volumen(self, delta):
        self.accion("/api/volume", {"delta": delta})

    def action_mezclar(self):
        self.accion("/api/shuffle-queue")

    def action_recap(self):
        """«e»: a las estadísticas (o de vuelta al reproductor), sin esperar a que cambie sola."""
        if self.vista == "recap":
            self.mostrar("reproductor")
        else:
            threading.Thread(target=self.traer_recap, kwargs={"avisar": True}, daemon=True).start()

    def action_aleatorio(self):
        s = self.estado or {}
        prendido = bool(s.get("shuffle"))
        self.accion("/api/shuffle", {"on": not prendido})
        if not prendido and s.get("mode") == "mpv" and not (s.get("library") or {}).get("songs"):
            self.notify("Aleatorio prendido. Todavía no hay canciones guardadas: "
                        "se van guardando a medida que suenan.")


def main():
    ap = argparse.ArgumentParser(description="Rockola en la terminal")
    ap.add_argument("--url", default="http://localhost:8000", help="dirección del servidor")
    ap.add_argument("--tema", default="gruvbox",
                    help="tema de colores: gruvbox, tokyo-night, nord, dracula, monokai, …")
    ap.add_argument("--ciclo", type=float, default=15, metavar="SEGUNDOS",
                    help="cada cuánto cambia sola entre el reproductor y las estadísticas (0: nunca)")
    args = ap.parse_args()
    Rockola(args.url, args.tema, args.ciclo).run()


if __name__ == "__main__":
    main()
