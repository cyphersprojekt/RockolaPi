#!/usr/bin/env python3
"""Botones físicos de la rockola, conectados al GPIO de la Raspberry.

    [ − ]  [ ⏮ ]  [ ⏯ ]  [ ⏭ ]  [ + ]

Doble toque en − es ⏮ y doble toque en + es ⏭ (por si alguno de esos dos se rompe, o para
armar la botonera con tres botones nada más).

Corre aparte del servidor y le habla por HTTP (localhost): si algo falla acá, la música
sigue. Se configura en la sección [controles] de rockola.toml. Conexiones: docs/botones.md

Usa el Python del sistema (en Raspberry Pi OS ya vienen gpiozero y lgpio):

    python3 controles.py            andando (lo arranca solo el servicio rockola-controles)
    python3 controles.py --probar   muestra qué botón se aprieta, sin tocar la música
"""
import argparse
import json
import threading
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

AQUI = Path(__file__).parent

# Pines en numeración BCM. Cada botón usa dos pines pegados en el conector: su señal y una
# tierra (GND) para él solo, así se cablea con dupont hembra-hembra sin soldar ni empalmar.
# Los pares van ordenados de izquierda a derecha, igual que los botones en el frente.
POR_DEFECTO = {
    "activado": True,
    "vol_menos": 25,    # pin 22 (tierra: pin 20)
    "anterior": 11,     # pin 23 (tierra: pin 25)
    "play": 5,          # pin 29 (tierra: pin 30)
    "siguiente": 13,    # pin 33 (tierra: pin 34)
    "vol_mas": 26,      # pin 37 (tierra: pin 39)
    "paso_volumen": 5,
    "doble_toque": 0.4,   # segundos entre dos toques de − o + para que cuenten como ⏮ / ⏭
}
BOTONES = ("vol_menos", "anterior", "play", "siguiente", "vol_mas")
REPITEN = {"vol_menos", "vol_mas"}   # mantenidos, siguen subiendo/bajando
DOBLE = {"vol_menos": "anterior", "vol_mas": "siguiente"}   # qué hace el doble toque


def log(*a):
    print(*a, flush=True)


class Rockola:
    """Cliente mínimo de la API del servidor."""

    def __init__(self, puerto):
        self.base = f"http://localhost:{puerto}"
        self.avisado = False

    def post(self, path, body=None):
        datos = json.dumps(body or {}).encode()
        req = urllib.request.Request(self.base + path, data=datos,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
            self.avisado = False
        except urllib.error.HTTPError as e:
            log(f"  {path}: {e.code} {e.read()[:120]!r}")
        except OSError as e:
            if not self.avisado:          # el servidor todavía no arrancó: avisar una vez
                log(f"  no hay conexión con la rockola ({e}); sigo intentando")
                self.avisado = True

    def en_segundo_plano(self, path, body=None):
        # Sin bloquear el hilo de gpiozero: así los botones responden aunque el servidor tarde.
        threading.Thread(target=self.post, args=(path, body), daemon=True).start()


def cargar_config(ruta):
    cfg, puerto = dict(POR_DEFECTO), 8000
    if ruta.exists():
        with open(ruta, "rb") as f:
            c = tomllib.load(f)
        puerto = int(c.get("puerto", puerto))
        cfg.update(c.get("controles", {}))
    return cfg, puerto


def acciones_de(rockola, paso):
    return {
        "vol_menos": lambda: rockola.en_segundo_plano("/api/volume", {"delta": -paso}),
        "anterior": lambda: rockola.en_segundo_plano("/api/prev"),
        "play": lambda: rockola.en_segundo_plano("/api/pause"),
        "siguiente": lambda: rockola.en_segundo_plano("/api/skip"),
        "vol_mas": lambda: rockola.en_segundo_plano("/api/volume", {"delta": paso}),
    }


def antirrebote(accion, minimo=0.15):
    """Ignora toques del mismo botón a menos de `minimo` segundos: los botones baratos
    "rebotan" al apretarlos y un toque podría contar varias veces."""
    ultimo = [-1e9]

    def filtrada():
        ahora = time.monotonic()
        if ahora - ultimo[0] >= minimo:
            ultimo[0] = ahora
            accion()
    return filtrada


class DobleToque:
    """Un botón con dos funciones: un toque hace `simple` y dos toques seguidos, `doble`.

    Para saber si viene un segundo toque hay que esperar `ventana` segundos, así que el
    toque simple sale con esa demora. Mantenido, repite `simple` como siempre.

    Después de un doble, los toques que siguen enseguida se ignoran: si alguien aprieta
    + varias veces rápido, saltea una sola canción y no tres.
    """

    def __init__(self, simple, doble, ventana, reloj=time.monotonic):
        self.simple, self.doble, self.ventana, self.reloj = simple, doble, ventana, reloj
        self.lock = threading.Lock()
        self.espera = None         # Timer del primer toque, mientras puede llegar el segundo
        self.ultimo_doble = -1e9   # último toque de una ráfaga que ya hizo doble
        self.ultimo_simple = -1e9
        self.de_un_doble = False   # el toque de ahora es parte de un doble (si lo mantienen, nada)

    def apretado(self):
        with self.lock:
            ahora = self.reloj()
            self.de_un_doble = True
            if ahora - self.ultimo_doble < self.ventana:   # sigue la ráfaga del doble
                self.ultimo_doble = ahora
                return
            if self.espera is None:                        # primer toque: esperar al segundo
                self.de_un_doble = False
                t = self.espera = threading.Timer(self.ventana, lambda: self._vencio(t))
                t.daemon = True
                t.start()
                return
            self.espera.cancel()                           # segundo toque a tiempo
            self.espera = None
            self.ultimo_doble = ahora
        self.doble()

    def _vencio(self, timer):
        with self.lock:
            if self.espera is not timer:   # llegó el segundo toque justo a tiempo
                return
            self.espera = None
            self.ultimo_simple = self.reloj()
        self.simple()

    def mantenido(self):
        with self.lock:
            ahora = self.reloj()
            if self.de_un_doble:
                return
            if self.espera is not None:    # todavía esperando el segundo toque: ya no viene
                self.espera.cancel()
                self.espera = None
            elif ahora - self.ultimo_simple < 0.25:
                return                     # el toque simple acaba de salir: no duplicarlo
            self.ultimo_simple = ahora
        self.simple()


def armar_botones(cfg, acciones, Button=None, dobles=None):
    """Crea los botones (hay que conservar la lista que devuelve, si no se liberan).

    `dobles`: qué hace el doble toque de − y + (por defecto, lo mismo que ⏮ y ⏭).
    """
    if Button is None:
        from gpiozero import Button
    ventana = float(cfg.get("doble_toque") or 0)
    dobles = dobles or {n: acciones[otro] for n, otro in DOBLE.items()}
    botones = []
    for nombre in BOTONES:
        pin = int(cfg.get(nombre) or 0)
        if not pin:
            continue
        # Botón entre el pin y GND: la resistencia pull-up interna lo deja en 1 sin apretar.
        b = Button(pin, pull_up=True, bounce_time=0.03, hold_time=0.4,
                   hold_repeat=nombre in REPITEN)
        if nombre in DOBLE and ventana > 0:
            d = DobleToque(acciones[nombre], dobles[nombre], ventana)
            b.when_pressed = antirrebote(d.apretado)
            b.when_held = d.mantenido
        else:
            b.when_pressed = antirrebote(acciones[nombre])
            if nombre in REPITEN:
                b.when_held = acciones[nombre]
        botones.append(b)
    return botones


def main():
    ap = argparse.ArgumentParser(description="Botones físicos de la rockola")
    ap.add_argument("--config", type=Path, default=AQUI / "rockola.toml")
    ap.add_argument("--probar", action="store_true", help="mostrar qué botón se aprieta, sin tocar la música")
    args = ap.parse_args()

    cfg, puerto = cargar_config(args.config)
    if not cfg["activado"]:
        log("Botones desactivados en rockola.toml ([controles] activado = false).")
        return

    dobles = None
    if args.probar:
        acciones = {n: (lambda n=n: log(f"  apretaste: {n}")) for n in BOTONES}
        dobles = {n: (lambda n=n, otro=otro: log(f"  apretaste: {n} dos veces → {otro}"))
                  for n, otro in DOBLE.items()}
        log("Apretá los botones (Ctrl+C para salir)…")
    else:
        acciones = acciones_de(Rockola(puerto), int(cfg["paso_volumen"]))
    botones = armar_botones(cfg, acciones, dobles=dobles)
    log("Botones: " + ", ".join(f"{n}=GPIO{cfg[n]}" for n in BOTONES if cfg.get(n)))
    if float(cfg.get("doble_toque") or 0) > 0:
        log("Doble toque: − dos veces = anterior, + dos veces = siguiente")
    try:
        while True:
            time.sleep(3600)   # gpiozero atiende los botones en su propio hilo
    except KeyboardInterrupt:
        pass
    del botones


if __name__ == "__main__":
    main()
