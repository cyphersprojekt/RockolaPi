"""Las tapas de los discos de las canciones guardadas, con una copia en la rockola.

Cada canción trae la dirección de su tapa, que está en los servidores de Google: sin internet la
música sigue (lo guardado suena igual), pero las páginas y la pantalla se quedan sin tapas. Acá
se baja una copia de las tapas de las canciones guardadas y, en todo lo que la rockola les
contesta a las páginas, la dirección de una tapa que está copiada se cambia por la de la copia
(/tapas/…): se ven aunque se corte internet, y vienen de la red de la oficina.

Una misma tapa llega con direcciones distintas según de dónde salió el tema (la búsqueda la da de
120 px, unos 8 KB; un álbum, de 544 px, diez veces más). Acá es una sola, de 120 px, que alcanza
para el tamaño en que se muestra, y los temas de un mismo disco comparten archivo.

Las copias son sólo eso: lo que queda anotado en cada canción, en las colas y en las
estadísticas es siempre la dirección original. Si se borra la carpeta, se vuelven a bajar.
"""
import hashlib
import http.client
import os
import re
import threading
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

LADO = 120                # píxeles de lado de la copia (las listas las muestran de unos 42, por 3 en un celular)
MAXIMO = 1024 * 1024      # bytes: más que eso no es una tapa
INTENTOS = 5              # veces que se prueba una a la que no se pudo llegar, antes de dejarla para más tarde
# Las de YouTube Music: …googleusercontent.com/<la imagen>=w544-h544-l90-rj (lo de después del = es el tamaño)
DE_GOOGLE = re.compile(r"(https://[a-z0-9-]+\.(?:googleusercontent|ggpht)\.com/[^=?#\s]+)(?:=[^/?#\s]*)?")
# Las de un video: i.ytimg.com/vi/<id>/hqdefault.jpg?… (el nombre y lo de después de ? son el tamaño)
DE_VIDEO = re.compile(r"https://i\d*\.ytimg\.com/vi(?:_webp)?/([A-Za-z0-9_-]{11})/[^/\s]+")
ARCHIVO = re.compile(r"([0-9a-f]{20})\.(jpg|png|webp|gif)")
TIPOS = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
# "thumb": "…" en un JSON recién armado por json.dumps (una dirección no lleva comillas ni barras invertidas)
EN_JSON = re.compile(r'("thumb": ")([^"\\]+)(")')


@lru_cache(maxsize=8192)
def copia_de(url):
    """De la dirección de una tapa: (la clave de su copia, de dónde bajarla). La clave es la misma
    para todos los tamaños de una imagen. None si no es una dirección de la web."""
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return None
    m = DE_GOOGLE.fullmatch(url)
    if m:
        de = f"{m[1]}=w{LADO}-h{LADO}-l90-rj"
    else:
        m = DE_VIDEO.fullmatch(url)
        de = f"https://i.ytimg.com/vi/{m[1]}/mqdefault.jpg" if m else url
    return hashlib.sha1(de.encode()).hexdigest()[:20], de


def _tipo(data):
    """La extensión que le toca a esa imagen, o None si no es una."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"GIF8":
        return "gif"
    return None


class Tapas:
    """Las copias, en `carpeta`: <clave>.jpg (o .png, .webp, .gif). Se bajan de a una, sin apuro,
    en un hilo aparte. `hay_red()`: si tiene sentido intentarlo ahora (sin internet, espera)."""

    def __init__(self, carpeta, hay_red=lambda: True, pausa=0.2, espera_sin_red=30, reintento=6 * 3600):
        self.carpeta = Path(carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.hay_red, self.pausa, self.espera_sin_red, self.reintento = hay_red, pausa, espera_sin_red, reintento
        self.lock = threading.Condition()
        self.archivos = {}      # clave -> el nombre de su archivo
        self.pendientes = {}    # clave -> (de dónde bajarla, la dirección original), en el orden en que se van a bajar
        self.intentos = {}      # clave -> cuántas veces no se pudo llegar a buscarla
        self.fallaron = {}      # clave -> hasta cuándo no insistir (no está, no es una imagen…)
        for f in self.carpeta.iterdir():
            m = ARCHIVO.fullmatch(f.name)
            if m:
                self.archivos[m[1]] = f.name
            elif f.is_file():   # restos (una que se estaba bajando cuando se cortó la luz)
                f.unlink(missing_ok=True)
        threading.Thread(target=self._bucle, daemon=True, name="tapas").start()

    # --- para las páginas

    def local(self, url):
        """La dirección de la copia de esa tapa (/tapas/…), o None si no hay."""
        c = copia_de(url)
        nombre = c and self.archivos.get(c[0])
        return f"/tapas/{nombre}" if nombre else None

    def en_json(self, texto):
        """Un JSON recién armado, con la dirección de la copia en las tapas ("thumb") que la
        tienen. Se hace sobre el texto, al mandarlo: los datos de adentro no se tocan."""
        if not self.archivos:
            return texto
        return EN_JSON.sub(lambda m: m[1] + (self.local(m[2]) or m[2]) + m[3], texto)

    def archivo(self, nombre):
        """Para servir /tapas/<nombre>: (la ruta, el tipo), o None si no es una copia de acá."""
        m = ARCHIVO.fullmatch(nombre)
        if not m or self.archivos.get(m[1]) != nombre:
            return None
        return self.carpeta / nombre, TIPOS[m[2]]

    # --- cuáles tiene que haber

    def guardar(self, urls):
        """Que haya copia de esas tapas: las que faltan quedan anotadas para bajarse."""
        ahora = time.monotonic()
        with self.lock:
            for url in urls:
                c = copia_de(url)
                if not c or c[0] in self.archivos or c[0] in self.pendientes:
                    continue
                if self.fallaron.get(c[0], 0) > ahora:   # no se pudo hace poco: todavía no
                    continue
                self.pendientes[c[0]] = (c[1], url)
            self.lock.notify_all()

    def quedarse_con(self, urls):
        """Borra las copias que no son de ninguna de esas tapas (las de canciones que ya no
        están guardadas). Devuelve cuántas borró."""
        quedan = {c[0] for c in map(copia_de, urls) if c}
        with self.lock:
            sobran = [(clave, nombre) for clave, nombre in self.archivos.items() if clave not in quedan]
            for clave, nombre in sobran:
                del self.archivos[clave]
                (self.carpeta / nombre).unlink(missing_ok=True)
            for clave in [c for c in self.pendientes if c not in quedan]:
                del self.pendientes[clave]
        return len(sobran)

    def faltan(self):
        """Cuántas quedan por bajar."""
        with self.lock:
            return len(self.pendientes)

    # --- bajarlas

    def _bucle(self):
        while True:
            with self.lock:
                while not self.pendientes:
                    self.lock.wait()
                clave, (de, url) = next(iter(self.pendientes.items()))
            if not self.hay_red():
                time.sleep(self.espera_sin_red)
                continue
            try:
                como = self._bajar(clave, de, url)
            except Exception as e:   # (que una rara no deje a las demás sin bajar)
                print(f"  ⚠ bajando una tapa: {e!r}", flush=True)
                como = "no"
            with self.lock:
                sin_llegar = como == "red" and clave in self.pendientes and self.intentos.get(clave, 0) + 1 < INTENTOS
                if sin_llegar:   # se habrá cortado internet: al final de la fila, y más despacio
                    self.intentos[clave] = self.intentos.get(clave, 0) + 1
                    self.pendientes[clave] = self.pendientes.pop(clave)
                else:
                    self.pendientes.pop(clave, None)
                    self.intentos.pop(clave, None)
                    if como != "ok":
                        self.fallaron[clave] = time.monotonic() + self.reintento
            time.sleep(self.espera_sin_red if sin_llegar else self.pausa)

    def _bajar(self, clave, de, url):
        """Baja una y la deja guardada. Devuelve "ok" si quedó, "red" si no se pudo llegar a
        buscarla (se prueba de nuevo en un rato) o "no" si no está o no es una imagen."""
        como = "no"
        for intento in dict.fromkeys((de, url)):   # la del tamaño que se guarda y, si no anda, la original
            try:
                pedido = urllib.request.Request(intento, headers={"User-Agent": "Mozilla/5.0 (rockola)"})
                with urllib.request.urlopen(pedido, timeout=15) as r:
                    data = r.read(MAXIMO + 1)
            except (urllib.error.HTTPError, ValueError):
                continue   # no está (o no con ese tamaño), o la dirección está mal: la otra
            except (OSError, http.client.HTTPException):
                como = "red"   # no se pudo llegar
                continue
            ext = _tipo(data)
            if not ext or len(data) > MAXIMO:
                continue
            nombre, tmp = f"{clave}.{ext}", self.carpeta / f"{clave}.tmp"
            try:
                tmp.write_bytes(data)
                os.replace(tmp, self.carpeta / nombre)   # de una: no queda una imagen a medias
            except OSError as e:
                tmp.unlink(missing_ok=True)
                print(f"  ⚠ no pude guardar una tapa en {self.carpeta}: {e}", flush=True)
                return "no"
            with self.lock:
                if clave in self.pendientes:   # (si no, mientras se bajaba dejó de hacer falta)
                    self.archivos[clave] = nombre
                else:
                    (self.carpeta / nombre).unlink(missing_ok=True)
            return "ok"
        return como
