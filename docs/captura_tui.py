#!/usr/bin/env python3
"""Saca la captura de la terminal para el README: docs/img/tui.png.

Corre la TUI (tui.py) sin terminal, contra una rockola que esté andando, y dibuja en un PNG lo
que pinta: las mismas celdas, con sus letras y sus colores. No hace falta un emulador de
terminal ni un navegador.

    .venv/bin/python docs/captura_tui.py                                  # la de http://localhost:8000
    .venv/bin/python docs/captura_tui.py --url http://rockola.local:8000 --salida /tmp/tui.png
    .venv/bin/python docs/captura_tui.py --vista estadisticas             # docs/img/tui_recap.png

En la captura sale el link de esa rockola (con su IP): si no la querés mostrar, arrancala con
`--ip 192.168.1.50` (como hace docs/capturas.py con la de prueba).

Usa Pillow (ya está en requirements.txt) y la letra DejaVu Sans Mono, que viene con casi
cualquier Linux (`sudo apt install fonts-dejavu-core`). Las de la web las saca docs/capturas.py.
"""
import argparse
import asyncio
import io
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

LETRAS = Path("/usr/share/fonts/truetype/dejavu")
EMOJIS = Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf")
ANCHO, ALTO = 10, 20      # píxeles de una celda (después se multiplican por --escala)
BLOQUES_ABAJO = "▁▂▃▄▅▆▇█"       # de 1/8 a 8/8, desde abajo
BLOQUES_IZQUIERDA = "▏▎▍▌▋▊▉"     # de 1/8 a 7/8, desde la izquierda


async def lo_que_pinta(url, tema, columnas, renglones, espera, estadisticas=False):
    """Corre la TUI sin terminal y devuelve los renglones de la pantalla (listas de Segment de
    Rich) y sus colores de base (fondo, texto). Con `estadisticas`, en esa vista."""
    from rich.color import Color
    from rich.console import Console
    from tui import Rockola

    app = Rockola(url, tema, ciclo=0)   # (que no cambie de vista sola justo ahora)
    async with app.run_test(size=(columnas, renglones)) as pilot:
        await pilot.pause(espera)   # que lleguen el estado, el link y la tapa del disco
        if not app.conectado:
            sys.exit(f"No hay una rockola andando en {url}")
        if estadisticas:
            app.action_recap()
            await pilot.pause(3)
            if app.vista != "recap":
                sys.exit("Esa rockola todavía no tiene estadísticas para mostrar")
        consola = Console(width=columnas, height=renglones, file=io.StringIO(), force_terminal=True,
                          color_system="truecolor", legacy_windows=False, safe_box=False)
        # (lo mismo que hace Textual para sus capturas en SVG: ver App.export_screenshot)
        pantalla = app.screen._compositor.render_update(full=True, screen_stack=app._background_screens,
                                                        simplify=True)
        lineas = consola.render_lines(pantalla, consola.options, pad=True)[:renglones]
        var = app.get_css_variables()
        base = tuple(tuple(Color.parse(var.get(k) or defecto).get_truecolor())
                     for k, defecto in (("background", "#282828"), ("foreground", "#ebdbb2")))
    return lineas, base


def dibujar(lineas, base, columnas, escala):
    """Las celdas, a una imagen. Los bloques (▀ ▄ █: la tapa y el QR) y las rayas (━ ─: el
    progreso) van como rectángulos, para que queden parejos; el resto, con la letra."""
    from PIL import Image, ImageDraw, ImageFont
    from rich.cells import cell_len

    cw, ch = ANCHO * escala, ALTO * escala
    try:
        # 16,6 px de letra por cada 10 de ancho de celda: lo que mide de ancho DejaVu Sans Mono
        normal = ImageFont.truetype(str(LETRAS / "DejaVuSansMono.ttf"), round(1.66 * cw))
        negrita = ImageFont.truetype(str(LETRAS / "DejaVuSansMono-Bold.ttf"), round(1.66 * cw))
    except OSError:
        sys.exit(f"Falta la letra DejaVu Sans Mono en {LETRAS}:  sudo apt install fonts-dejavu-core")
    try:
        emojis = ImageFont.truetype(str(EMOJIS), 109)   # (es de mapa de bits: viene en ese tamaño)
    except OSError:
        emojis = None
    subida, bajada = normal.getmetrics()
    renglon = (ch + subida - bajada) // 2   # dónde apoya la letra dentro de la celda
    fondo0, texto0 = base
    img = Image.new("RGB", (columnas * cw, len(lineas) * ch), fondo0)
    d = ImageDraw.Draw(img)

    def color(c, defecto):
        return tuple(c.get_truecolor()) if c is not None and not c.is_default else defecto

    for fila, linea in enumerate(lineas):
        x, y = 0, fila * ch
        for seg in linea:
            if seg.control:
                continue
            st = seg.style
            texto, fondo = (color(st.color, texto0), color(st.bgcolor, fondo0)) if st else (texto0, fondo0)
            if st and st.reverse:
                texto, fondo = fondo, texto
            if st and st.dim:   # a media luz: a mitad de camino del fondo
                texto = tuple((a + b) // 2 for a, b in zip(texto, fondo))
            letra = negrita if st and st.bold else normal
            for c in seg.text:
                n = cell_len(c)
                if not n:
                    continue
                w = n * cw
                if fondo != fondo0:
                    d.rectangle([x, y, x + w - 1, y + ch - 1], fill=fondo)
                if c == "▀":
                    d.rectangle([x, y, x + w - 1, y + ch // 2 - 1], fill=texto)
                elif c in BLOQUES_ABAJO:
                    alto = ch * (BLOQUES_ABAJO.index(c) + 1) // 8
                    d.rectangle([x, y + ch - alto, x + w - 1, y + ch - 1], fill=texto)
                elif c in BLOQUES_IZQUIERDA:
                    d.rectangle([x, y, x + w * (BLOQUES_IZQUIERDA.index(c) + 1) // 8 - 1, y + ch - 1], fill=texto)
                elif c == "▐":
                    d.rectangle([x + w // 2, y, x + w - 1, y + ch - 1], fill=texto)
                elif c in "━─":
                    grosor = max(1, round(ch * (0.14 if c == "━" else 0.05)))
                    arriba = y + (ch - grosor) // 2
                    d.rectangle([x, arriba, x + w - 1, arriba + grosor - 1], fill=texto)
                elif n == 2 and emojis:   # un emoji (🔀): con la letra de emojis, achicado a dos celdas
                    lado = min(w, ch) - 2 * escala
                    e = Image.new("RGBA", (136, 128), (0, 0, 0, 0))
                    ImageDraw.Draw(e).text((0, 0), c, font=emojis, embedded_color=True)
                    e = e.crop(e.getbbox() or (0, 0, 1, 1)).resize((lado, lado), Image.LANCZOS)
                    img.paste(e, (x + (w - lado) // 2, y + (ch - lado) // 2), e)
                elif c != " ":
                    d.text((x, y + renglon), c, font=letra, fill=texto, anchor="ls")
                if st and st.underline:
                    d.rectangle([x, y + renglon + 2 * escala, x + w - 1, y + renglon + 3 * escala - 1], fill=texto)
                x += w
    return img


def main():
    ap = argparse.ArgumentParser(description="Captura de la TUI de la rockola, para el README")
    ap.add_argument("--url", default="http://localhost:8000", help="la rockola que se va a mostrar")
    ap.add_argument("--vista", choices=["reproductor", "estadisticas"], default="reproductor",
                    help="cuál de las dos vistas de la TUI")
    ap.add_argument("--salida", type=Path, help="por defecto, docs/img/tui.png (o tui_recap.png)")
    ap.add_argument("--tamano", default="118x34", help="columnas x renglones de la terminal")
    ap.add_argument("--tema", default="gruvbox", help="el --tema de tui.py")
    ap.add_argument("--espera", type=float, default=6, help="segundos hasta sacarla (que baje la tapa del disco)")
    ap.add_argument("--escala", type=int, default=2, help="píxeles por punto (2: se ve nítida en pantallas de alta densidad)")
    args = ap.parse_args()
    try:
        columnas, renglones = (int(n) for n in args.tamano.lower().split("x"))
    except ValueError:
        sys.exit("--tamano va como 118x34")
    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("Falta Pillow:  .venv/bin/python -m pip install pillow")

    estadisticas = args.vista == "estadisticas"
    salida = args.salida or RAIZ / "docs" / "img" / ("tui_recap.png" if estadisticas else "tui.png")
    lineas, base = asyncio.run(lo_que_pinta(args.url.rstrip("/"), args.tema, columnas, renglones, args.espera, estadisticas))
    img = dibujar(lineas, base, columnas, args.escala)
    salida.parent.mkdir(parents=True, exist_ok=True)
    img.save(salida, optimize=True)
    print(f"✓ {salida} ({img.width} × {img.height})")


if __name__ == "__main__":
    main()
