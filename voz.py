"""La voz de la rockola: anuncios grabados que se repiten, y el micrófono abierto.

- **HTTPS con certificado propio** (`certificado`): los navegadores sólo dejan usar el micrófono en
  páginas seguras. La rockola abre un segundo puerto con un certificado autofirmado, y la página
  habilita el micrófono sólo ahí: quien entra por http:// no ve ninguna advertencia, y quien
  quiere hablar toca un botón que lo lleva al https://.
- **Anuncios** (`Voz.anuncio_*`): audios que se graban en la página (o se suben), con "cada X
  minutos" y/o "cada X canciones". Suenan encima de la música, que baja de volumen mientras tanto.
- **Micrófono abierto** (`Voz.mic_*`): la página manda el audio en pedacitos (MediaRecorder) y
  un mpv aparte lo toca en vivo, con la música más baja. Se cierra solo si dejan de llegar.

Todo es de la sala que suena en el parlante, y de quien tiene el PIN.
"""
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

MAX_ANUNCIO = 30 * 1024 * 1024    # bytes de un anuncio
MAX_ANUNCIOS = 30
MAX_CHUNK = 1024 * 1024
MIC_VENCE = 6                     # segundos sin recibir nada: se cierra el micrófono
ID = re.compile(r"[0-9a-f]{10}")


def certificado(carpeta, ip):
    """(certificado, clave) autofirmados, en `carpeta`; se hacen la primera vez y cuando cambia la
    IP (el certificado la lleva, para que el nombre coincida). None si no se pudo (sin openssl)."""
    carpeta = Path(carpeta)
    cert, clave, marca = carpeta / "cert.pem", carpeta / "clave.pem", carpeta / "ip.txt"
    if cert.exists() and clave.exists() and marca.exists() and marca.read_text() == ip:
        return cert, clave
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                        "-keyout", str(clave), "-out", str(cert), "-subj", "/CN=rockola",
                        "-addext", f"subjectAltName=IP:{ip},DNS:rockola.local,DNS:localhost"],
                       check=True, capture_output=True, timeout=60)
        os.chmod(clave, 0o600)
        marca.write_text(ip)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  ⚠ no pude hacer el certificado https: {e}", flush=True)
        return None
    return cert, clave


class ErrorVoz(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


class Voz:
    """`bajar(on)`: baja/sube la música mientras suena la voz. `args_mpv`: las opciones de audio de
    la rockola. `lanzar`: cómo crear un proceso (ver reproductor._lanzar). `al_cambiar()`: avisa a
    las páginas."""

    def __init__(self, carpeta, bajar, lanzar, args_mpv=(), al_cambiar=lambda: None, mpv="mpv"):
        self.carpeta = Path(carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.bajar, self.lanzar, self.args_mpv, self.al_cambiar, self.mpv = bajar, lanzar, list(args_mpv), al_cambiar, mpv
        self.lock = threading.RLock()
        self.anuncios = {}
        self.hablando = 0          # cuántas voces suenan (para bajar la música una sola vez)
        self.mic = None            # {"proc", "visto"}
        self.canciones = 0         # canciones empezadas (para los "cada X canciones")
        self.suena = None          # id del anuncio que suena
        for js in self.carpeta.glob("*.json"):
            try:
                a = json.loads(js.read_text())
                if ID.fullmatch(a["id"]) and (self.carpeta / a["archivo"]).is_file():
                    self.anuncios[a["id"]] = a
            except (OSError, ValueError, KeyError, TypeError):
                pass
        threading.Thread(target=self._bucle, daemon=True, name="voz").start()

    # --- lo que ven las páginas

    def estado(self):
        with self.lock:
            return {"mic": self.mic is not None, "suena": self.suena,
                    "anuncios": sorted((dict(a, archivo=None) for a in self.anuncios.values()), key=lambda a: a["creado"])}

    # --- anuncios

    def _escribir(self, a):
        js = self.carpeta / f"{a['id']}.json"
        tmp = js.with_suffix(".tmp")
        tmp.write_text(json.dumps(a, ensure_ascii=False))
        os.replace(tmp, js)

    @staticmethod
    def _num(v, tope):
        try:
            return max(0, min(tope, int(v)))
        except (TypeError, ValueError):
            return 0

    def anuncio_nuevo(self, data, nombre, cada_min, cada_canciones, por=""):
        if not data or len(data) > MAX_ANUNCIO:
            raise ErrorVoz(413, f"El audio tiene que pesar entre 1 byte y {MAX_ANUNCIO // 2**20} MB")
        with self.lock:
            if len(self.anuncios) >= MAX_ANUNCIOS:
                raise ErrorVoz(429, f"Ya hay {MAX_ANUNCIOS} anuncios: borrá alguno")
            aid = uuid.uuid4().hex[:10]
            archivo = f"{aid}.audio"
            (self.carpeta / archivo).write_bytes(data)
            a = {"id": aid, "archivo": archivo, "nombre": " ".join(str(nombre or "").split())[:60] or "Anuncio",
                 "cada_min": self._num(cada_min, 24 * 60), "cada_canciones": self._num(cada_canciones, 200),
                 "activo": True, "creado": time.time(), "ultimo": time.time(),
                 "desde": self.canciones, "por": str(por)[:30]}
            self.anuncios[aid] = a
            self._escribir(a)
        self.al_cambiar()
        return a

    def anuncio_editar(self, aid, cambios):
        with self.lock:
            a = self._de(aid)
            if "nombre" in cambios:
                a["nombre"] = " ".join(str(cambios["nombre"]).split())[:60] or a["nombre"]
            if "cada_min" in cambios:
                a["cada_min"] = self._num(cambios["cada_min"], 24 * 60)
            if "cada_canciones" in cambios:
                a["cada_canciones"] = self._num(cambios["cada_canciones"], 200)
            if "activo" in cambios:
                a["activo"] = bool(cambios["activo"])
            a["ultimo"], a["desde"] = time.time(), self.canciones   # (los "cada X" cuentan desde el cambio)
            self._escribir(a)
        self.al_cambiar()

    def anuncio_borrar(self, aid):
        with self.lock:
            a = self._de(aid)
            del self.anuncios[aid]
            for f in (a["archivo"], f"{aid}.json"):
                (self.carpeta / f).unlink(missing_ok=True)
        self.al_cambiar()

    def _de(self, aid):
        a = self.anuncios.get(str(aid))
        if a is None:
            raise ErrorVoz(404, "Ese anuncio ya no existe")
        return a

    def anuncio_sonar(self, aid):
        """Lo hace sonar ya (para probarlo)."""
        with self.lock:
            a = self._de(aid)
            if self.suena:
                raise ErrorVoz(409, "Ya está sonando un anuncio")
            self.suena = a["id"]
        threading.Thread(target=self._sonar, args=(a,), daemon=True, name="anuncio").start()
        self.al_cambiar()

    def cancion_nueva(self):
        """Empezó una canción en la sala del parlante."""
        with self.lock:
            self.canciones += 1

    def _sonar(self, a):
        try:
            self._subir(+1)
            p = self.lanzar([self.mpv, "--no-config", "--no-video", "--really-quiet", *self.args_mpv,
                             str(self.carpeta / a["archivo"])], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                p.wait(timeout=300)
            except subprocess.TimeoutExpired:
                p.kill()
        except Exception as e:
            print(f"  ⚠ no pude pasar el anuncio: {e!r}", flush=True)
        finally:
            with self.lock:
                a["ultimo"], a["desde"] = time.time(), self.canciones
                try:
                    if a["id"] in self.anuncios:
                        self._escribir(a)
                except OSError:
                    pass
                self.suena = None
            self._subir(-1)
            self.al_cambiar()

    def _subir(self, delta):
        """Cuenta las voces que suenan: la música baja con la primera y sube con la última."""
        with self.lock:
            antes = self.hablando
            self.hablando = max(0, antes + delta)
            cambia = (antes == 0) != (self.hablando == 0)
            hablando = self.hablando > 0
        if cambia:
            try:
                self.bajar(hablando)
            except Exception as e:
                print(f"  ⚠ no pude bajar la música: {e!r}", flush=True)

    # --- micrófono

    def mic_abrir(self):
        with self.lock:
            if self.mic:
                return
            p = self.lanzar([self.mpv, "--no-config", "--no-video", "--really-quiet", "--profile=low-latency",
                             "--cache=no", "--demuxer-readahead-secs=0", "--audio-buffer=0.1", *self.args_mpv, "-"],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.mic = {"proc": p, "visto": time.monotonic()}
        self._subir(+1)
        self.al_cambiar()

    def mic_pedazo(self, data):
        with self.lock:
            m = self.mic
            if not m:
                raise ErrorVoz(409, "El micrófono no está abierto")
            m["visto"] = time.monotonic()
        try:
            m["proc"].stdin.write(data)
            m["proc"].stdin.flush()
        except (OSError, ValueError):
            self.mic_cerrar()
            raise ErrorVoz(409, "El micrófono se cortó")

    def mic_cerrar(self):
        with self.lock:
            m, self.mic = self.mic, None
        if not m:
            return
        try:
            m["proc"].stdin.close()
        except (OSError, ValueError):
            pass
        threading.Timer(2, lambda: m["proc"].poll() is None and m["proc"].kill()).start()
        self._subir(-1)
        self.al_cambiar()

    # --- cuándo suena cada anuncio

    def _bucle(self):
        while True:
            time.sleep(2)
            with self.lock:
                m = self.mic
                if m and time.monotonic() - m["visto"] > MIC_VENCE:
                    m = "vencido"
                libre = not self.suena and not self.mic
                toca = next((a for a in sorted(self.anuncios.values(), key=lambda a: a["ultimo"]) if self._toca(a)), None) if libre else None
            if m == "vencido":
                self.mic_cerrar()
            elif toca:
                try:
                    self.anuncio_sonar(toca["id"])
                except ErrorVoz:
                    pass

    def _toca(self, a):
        if not a["activo"]:
            return False
        if a["cada_min"] and time.time() - a["ultimo"] >= a["cada_min"] * 60:
            return True
        return bool(a["cada_canciones"]) and self.canciones - a["desde"] >= a["cada_canciones"]
