#!/usr/bin/env python3
"""Saca las capturas de la web para el README: docs/img/web.png, web_control.png,
pantalla.png y player.png.

Levanta dos servidores de prueba en puertos aparte (no toca tu rockola), les agrega canciones
de ejemplo y saca las fotos con Chrome sin ventana. No suena nada: mpv manda el audio a ningún
lado. En las capturas aparece una IP de ejemplo, no la tuya.

    .venv/bin/python -m pip install playwright
    .venv/bin/python docs/capturas.py

Usa el Google Chrome que tengas instalado. Si no tenés, antes:  .venv/bin/playwright install chromium
Necesita mpv e internet (para buscar y bajar las canciones de ejemplo).
"""
import json
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
IMG = RAIZ / "docs" / "img"
IP_EJEMPLO = "192.168.1.50"
PIN = "1234"
CANCIONES = [
    ("soda stereo en la ciudad de la furia", "Juli"),
    ("daft punk get lucky", "Franco"),
    ("bad bunny baile inolvidable", "Azul"),
    ("taylor swift anti-hero", "Lean"),
    ("queen bohemian rhapsody", "Mati"),
    ("shakira ojos asi", "Juli"),
]


def api(base, path, body=None):
    datos = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=datos,
                                 headers={"Content-Type": "application/json"} if datos else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def levantar(modo, puerto, carpeta):
    """Servidor de prueba con su propia configuración (audio de mpv a ningún lado)."""
    config = carpeta / f"{modo}.toml"
    config.write_text(f'pin = "{PIN}"\npuerto = {puerto}\nmax_por_persona = 10\n'
                      f'carpeta = {json.dumps(str(carpeta / modo))}\n'   # no tocar tus canciones guardadas
                      f'mpv_args = ["--ao=null"]\n[controles]\nactivado = false\n')
    proc = subprocess.Popen([sys.executable, str(RAIZ / "server.py"), "--config", str(config),
                             "--modo", modo, "--ip", IP_EJEMPLO],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://localhost:{puerto}"
    for _ in range(100):
        try:
            api(base, "/api/info")
            return proc, base
        except OSError:
            if proc.poll() is not None:
                sys.exit(f"El servidor de prueba ({modo}) no arrancó. ¿Está instalado mpv?")
            time.sleep(0.2)
    sys.exit("El servidor de prueba no respondió")


def cargar_canciones(base, cuantas):
    for q, quien in CANCIONES[:cuantas]:
        resultados = api(base, "/api/search?q=" + urllib.parse.quote(q))
        if resultados:
            api(base, "/api/queue", {"videoId": resultados[0]["videoId"], "by": quien,
                                     "client": quien.lower()})
            print(f"  + {resultados[0]['title']} ({quien})")


def esperar_que_suene(base, segundos=20, limite=120):
    """Espera a que la primera canción lleve un rato sonando (así la barra de progreso se ve)."""
    t0 = time.time()
    while time.time() - t0 < limite:
        s = api(base, "/api/state")
        if s["now"] and s["progress"]["playing"] and s["progress"]["t"] >= segundos:
            return
        time.sleep(1)
    print("  (no llegó a sonar del todo; las capturas pueden salir sin barra de progreso)")


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Falta playwright:  .venv/bin/python -m pip install playwright")
    IMG.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        tmp = Path(tmp)
        try:
            navegador = p.chromium.launch(channel="chrome")
        except Exception:
            navegador = p.chromium.launch()   # el de playwright, si lo instalaste
        celular = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                       is_mobile=True, has_touch=True)

        print("Modo mpv (sin sonido): pedidos y /pantalla")
        proc, base = levantar("mpv", 8791, tmp)
        try:
            cargar_canciones(base, 6)
            esperar_que_suene(base)

            # 1. Pedidos, en el celular, con una búsqueda hecha
            ctx = navegador.new_context(**celular)
            ctx.add_init_script("localStorage.setItem('name', 'Juli')")
            pag = ctx.new_page()
            pag.goto(base + "/")
            pag.wait_for_selector("#now .titulo")
            pag.fill("#q", "soda stereo")
            pag.press("#q", "Enter")
            # Las guardadas aparecen enseguida: esperar también a lo de YouTube.
            pag.wait_for_function("document.querySelector('#results .fila') && "
                                  "!document.querySelector('#results .hint')?.textContent.includes('buscando')",
                                  timeout=60000)
            pag.wait_for_timeout(2000)                       # que carguen las tapas
            pag.locator("#search").scroll_into_view_if_needed()
            pag.evaluate("window.scrollBy(0, -70)")
            pag.wait_for_timeout(300)
            pag.screenshot(path=str(IMG / "web.png"))
            ctx.close()
            print("  ✓ docs/img/web.png")

            # 2. Pedidos con el PIN puesto: aparecen los controles
            ctx = navegador.new_context(**celular)
            ctx.add_init_script("localStorage.setItem('name', 'Juli'); "
                                f"localStorage.setItem('pins', JSON.stringify({{principal: '{PIN}'}}))")
            pag = ctx.new_page()
            pag.goto(base + "/")
            pag.wait_for_selector("#controls:not([hidden])")
            pag.wait_for_timeout(2000)
            pag.screenshot(path=str(IMG / "web_control.png"))
            ctx.close()
            print("  ✓ docs/img/web_control.png")

            # 3. /pantalla, en una tele
            ctx = navegador.new_context(viewport={"width": 1280, "height": 720})
            pag = ctx.new_page()
            pag.goto(base + "/pantalla")
            pag.wait_for_function("document.querySelector('#title').textContent.length > 0")
            pag.wait_for_timeout(2500)
            pag.screenshot(path=str(IMG / "pantalla.png"))
            ctx.close()
            print("  ✓ docs/img/pantalla.png")
        finally:
            proc.terminate()
            proc.wait(10)

        print("Modo navegador: /player")
        proc, base = levantar("navegador", 8792, tmp)
        try:
            cargar_canciones(base, 4)
            ctx = navegador.new_context(viewport={"width": 1280, "height": 760})
            pag = ctx.new_page()
            pag.goto(base + "/player")
            pag.click("#startBtn")                          # el toque que pide el navegador
            pag.wait_for_function("document.querySelector('#now .titulo') !== null")
            pag.wait_for_timeout(5000)                       # que cargue el video
            pag.screenshot(path=str(IMG / "player.png"))
            ctx.close()
            print("  ✓ docs/img/player.png")
        finally:
            proc.terminate()
            proc.wait(10)
        navegador.close()
    print("Listo. Revisalas en docs/img/ antes de subir.")


if __name__ == "__main__":
    main()
