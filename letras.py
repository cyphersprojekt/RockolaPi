"""Las letras de las canciones guardadas, con una copia en la rockola.

La letra de un tema se le pregunta a YouTube Music (o a LRCLIB) cuando empieza a sonar: sin
internet, lo guardado suena igual pero sin letra. Acá queda una copia de la letra de cada canción
guardada (`<videoId>.json`, unos pocos KB, tal cual se le manda a la página): la que ya está
copiada no se vuelve a preguntar, y se ve aunque se corte internet.

Las copias se hacen cuando alguien pide la letra de una guardada (ver `letra` en server.py) y,
para las que nadie pidió todavía, de a una y muy despacio en un hilo aparte: son un par de
pedidos a YouTube Music por canción, y con cientos de guardadas no hay ningún apuro.

Una canción sin letra (o con la letra sin tiempos) también queda anotada, para no preguntar cada
vez; pero al mes vence, por si mientras tanto alguien la subió.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

VENCE = 30 * 24 * 3600    # segundos que vale la copia de una sin letra, o con la letra sin tiempos
INTENTOS = 3              # veces que se prueba una a la que no se pudo llegar, antes de dejarla para más tarde
ARCHIVO = re.compile(r"([A-Za-z0-9_-]{11})\.json")


class Letras:
    """Las copias, en `carpeta`. `traer(video_id)` es quien consigue una letra y la deja copiada
    (con `guardar`); contesta "ok" (quedó, preguntando afuera), "ya" (quedó, sin preguntar: ya se
    sabía), "red" (no se pudo llegar: probar en un rato) o "no" (no se pudo: más tarde).
    `hay_red()`: si tiene sentido intentarlo ahora (sin internet, espera). `pausa`: segundos entre
    una pregunta afuera y la siguiente."""

    def __init__(self, carpeta, traer, hay_red=lambda: True, pausa=10, espera_sin_red=30, reintento=6 * 3600):
        self.carpeta = Path(carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.traer, self.hay_red = traer, hay_red
        self.pausa, self.espera_sin_red, self.reintento = pausa, espera_sin_red, reintento
        self.lock = threading.Condition()
        self.pendientes = {}    # videoId -> cuántas veces no se pudo llegar a buscarla (en el orden en que se van a buscar)
        self.fallaron = {}      # videoId -> hasta cuándo no insistir
        for f in self.carpeta.glob("*.tmp"):   # restos (una que se estaba escribiendo cuando se cortó la luz)
            f.unlink(missing_ok=True)
        threading.Thread(target=self._bucle, daemon=True, name="letras").start()

    def _ruta(self, video_id):
        return self.carpeta / f"{video_id}.json"

    def leer(self, video_id):
        """(la letra copiada, si venció), o (None, False) si no hay copia. Vencida: no tiene
        letra, o no tiene tiempos, y se anotó hace más de un mes (vale la pena preguntar de nuevo)."""
        ruta = self._ruta(video_id)
        try:
            datos = json.loads(ruta.read_text())
            cuando = ruta.stat().st_mtime
        except (OSError, ValueError):
            return None, False
        if not isinstance(datos, dict) or not isinstance(datos.get("lines"), list):
            return None, False
        return datos, not datos.get("synced") and time.time() - cuando > VENCE

    def guardar(self, video_id, datos):
        """Deja la copia (o la renueva: vale desde ahora)."""
        ruta = self._ruta(video_id)
        tmp = ruta.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(datos, ensure_ascii=False))
            os.replace(tmp, ruta)   # de una: un corte de luz no deja una letra a medias
        except OSError as e:
            tmp.unlink(missing_ok=True)
            print(f"  ⚠ no pude guardar una letra en {self.carpeta}: {e}", flush=True)

    def completar(self, ids, limpiar=False):
        """Que haya copia de la letra de esas canciones (las guardadas): las que faltan quedan
        anotadas para buscarse. `limpiar`: además, borrar las de las que no están en `ids`."""
        ids = set(ids)
        try:
            hay = {m[1] for m in map(ARCHIVO.fullmatch, os.listdir(self.carpeta)) if m}
        except OSError:
            return
        if limpiar:
            for video_id in hay - ids:
                self._ruta(video_id).unlink(missing_ok=True)
        ahora = time.monotonic()
        with self.lock:
            if limpiar:
                for video_id in [v for v in self.pendientes if v not in ids]:
                    del self.pendientes[video_id]
            for video_id in sorted(ids - hay):
                if video_id not in self.pendientes and self.fallaron.get(video_id, 0) <= ahora:
                    self.pendientes[video_id] = 0
            self.lock.notify_all()

    def faltan(self):
        """Cuántas quedan por buscar."""
        with self.lock:
            return len(self.pendientes)

    def _bucle(self):
        while True:
            with self.lock:
                while not self.pendientes:
                    self.lock.wait()
                video_id = next(iter(self.pendientes))
            if not self.hay_red():
                time.sleep(self.espera_sin_red)
                continue
            try:
                como = self.traer(video_id)
            except Exception as e:   # (que una rara no deje a las demás sin buscar)
                print(f"  ⚠ buscando la letra de {video_id}: {e!r}", flush=True)
                como = "no"
            with self.lock:
                veces = self.pendientes.get(video_id)
                sin_llegar = como == "red" and veces is not None and veces + 1 < INTENTOS
                if sin_llegar:   # se habrá cortado internet: al final de la fila, y más despacio
                    del self.pendientes[video_id]
                    self.pendientes[video_id] = veces + 1
                else:
                    self.pendientes.pop(video_id, None)
                    if como not in ("ok", "ya"):
                        self.fallaron[video_id] = time.monotonic() + self.reintento
            if como != "ya":   # (si no hubo que preguntar afuera, a la que sigue sin esperar)
                time.sleep(self.espera_sin_red if sin_llegar else self.pausa)
