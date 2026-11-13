#!/usr/bin/env python3
"""Rockola · pantalla de terminal.

Muestra qué suena, la cola y un QR para pedir temas. Habla con el servidor por
HTTP, así que se puede abrir en la misma máquina o por SSH.

    python tui.py                              # servidor en http://localhost:8000
    python tui.py --url http://otra-pc:8000    # sólo mirar (los controles andan desde localhost)

Teclas: espacio pausa · n siguiente · a anterior · ↑↓ elegir en la cola · t subir · d quitar · + - volumen
        m mezclar la cola · z aleatorio (con la cola vacía, suenan canciones guardadas al azar) · q salir
"""
import argparse
import io
import json
import threading
import time
import urllib.error
import urllib.request

from rich.color import Color
from rich.style import Style
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
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self, base, tema):
        super().__init__()
        self.base = base.rstrip("/")
        self.tema_inicial = tema
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

    def on_resize(self, _):
        self.firma_cola = None
        self.pintar_todo()

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
        self.pintar_todo()

    def sin_conexion(self):
        self.conectado = False
        self.pintar_cabecera()

    # --- dibujo

    def lado_tapa(self):
        return 32 if self.size.height >= 38 else 24 if self.size.height >= 26 else 16

    def pintar_todo(self):
        self.pintar_cabecera()
        self.pintar_arte()
        self.pintar_info()
        self.pintar_qr_lateral()
        self.pintar_cola()

    def pintar_cabecera(self):
        t = Text()
        t.append(" ♫ ROCKOLA ", style="bold reverse")
        if not self.conectado:
            t.append(f"  ⚠ sin conexión con {self.base} · reintentando…", style="bold red")
        elif self.url_publica:
            t.append("  pedí tu tema en ", style="dim")
            t.append(self.url_publica, style="bold underline")
        if self.estado and self.estado.get("mode") == "navegador":
            t.append("   (modo navegador: pausa y volumen desde /player)", style="dim")
        elif self.estado and self.estado.get("shuffle"):
            t.append("   🔀 al azar", style=f"bold {self.acento}")
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
            t.append("🔀 al azar, de las guardadas\n\n", style="bold")
        else:
            t.append("pedida por ", style="dim")
            t.append(now["by"] + "\n\n", style="bold")

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

    def pintar_qr_lateral(self):
        panel = self.query_one("#qr", Static)
        ancho_ok = self.size.width >= 110
        hay_musica = bool((self.estado or {}).get("now"))
        qr = qr_bloques(self.url_publica) if (self.url_publica and ancho_ok and hay_musica) else None
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

        firma = (tuple((i["id"], i["by"]) for i in cola), self.size.width)
        if firma == self.firma_cola:
            return
        self.firma_cola = firma
        tabla = self.query_one(DataTable)
        elegida = self.id_elegido()
        tabla.clear(columns=True)
        libre = max(20, self.size.width - 2 - 4 - 6 - 14 - 10)  # #, dur, pidió y separaciones
        w_tit, w_art = int(libre * 0.6), libre - int(libre * 0.6)
        tabla.add_column("#", width=3)
        tabla.add_column("Canción", width=w_tit)
        tabla.add_column("Artista", width=w_art)
        tabla.add_column("Dur.", width=5)
        tabla.add_column("Pidió", width=12)
        ids = []
        for n, it in enumerate(cola, 1):
            # Text(...) y no str: DataTable interpreta los str como markup, y un
            # compañero llamado "[/]" rompería la pantalla.
            tabla.add_row(Text(str(n)), Text(cortar(it["title"], w_tit)), Text(cortar(it["artist"], w_art)),
                          Text(fmt(it["duration"])), Text(cortar(it["by"], 12)), key=it["id"])
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
            self.accion("/api/next", {"finished": now["id"]})

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
    args = ap.parse_args()
    Rockola(args.url, args.tema).run()


if __name__ == "__main__":
    main()
