"""La voz de la rockola: anuncios grabados que se repiten, y el micrófono abierto.

- **HTTPS con certificado propio** (`certificado`): los navegadores sólo dejan usar el micrófono en
  páginas seguras. La rockola abre un segundo puerto con un certificado autofirmado, y la página
  habilita el micrófono sólo ahí: quien entra por http:// no ve ninguna advertencia, y quien
  quiere hablar toca un botón que lo lleva al https://.
- **Anuncios** (`Voz.anuncio_*`): audios que se graban en la página (o se suben), con "cada X
  minutos" y/o "cada X canciones", al azar, o a horas fijas ("14:00", o "*:00" = al minuto 0 de cada
  hora). Suenan encima de la música, que baja de volumen mientras tanto.
- **Micrófono abierto** (`Voz.mic_*`): la página manda el audio en pedacitos (MediaRecorder) y
  un mpv aparte lo toca en vivo, con la música más baja. Se cierra solo si dejan de llegar.

Todo es **por sala** y de quien tiene el PIN de esa sala. En la del parlante suena ahí (con la
música más baja); en todas, además, lo oyen los que escuchan por 📻 radio: la página baja el anuncio
(con `t`, el segundo en que va, para entrar a la par) y el micrófono llega en pedacitos por
`mic_leer`. Las salas sin parlante sólo tienen radio.
"""
import json
import os
import random
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

MAX_ANUNCIO = 30 * 1024 * 1024    # bytes de un anuncio
MAX_ANUNCIOS = 30
MAX_CHUNK = 1024 * 1024
MAX_PEDAZOS = 4000                # pedazos de micrófono que se recuerdan para la radio (unos 20 min: quien se suma a mitad de
                                  # camino necesita todos desde el encabezado, que los pedazos del medio solos no se decodifican)
PASO = 2                          # segundos entre una mirada y otra a los anuncios (ver _bucle)
MIC_VENCE = 6                     # segundos sin recibir nada: se cierra el micrófono
ID = re.compile(r"[0-9a-f]{10}")
MAX_HORARIOS = 48


def horarios(texto):
    """De lo que se escribe ("14, 18:30, *:00, :15") a la lista normalizada de "HH:MM" (todos los días a esa
    hora) y "*:MM" (al minuto MM de cada hora). "14" es 14:00; ":00" o "*:00", al minuto 0 de cada hora.
    Lo que no se entiende se ignora."""
    if isinstance(texto, (list, tuple)):
        texto = ",".join(map(str, texto))
    salida = []
    for t in re.split(r"[\s,;]+", str(texto or "")):
        m = re.fullmatch(r"(\d{1,2}|\*)?(?:[:.h](\d{1,2}))?", t.strip().lower().replace("hs", ""))
        if not t or not m or not (m[1] or m[2] is not None):
            continue
        h, mi = m[1], int(m[2] or 0)
        if mi > 59 or (h not in (None, "*") and int(h) > 23):
            continue
        h = "*" if h in (None, "*") else f"{int(h):02d}"
        if f"{h}:{mi:02d}" not in salida:
            salida.append(f"{h}:{mi:02d}")
    return sorted(salida)[:MAX_HORARIOS]


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


def _morir_con_el_padre():
    """Que mpv reciba SIGTERM si el servidor muere (no quedan mpv huérfanos)."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
    except Exception:
        pass


class MpvFijo:
    """Un mpv que queda siempre levantado, en espera (`--idle`), sólo para los anuncios del parlante. Levantar
    uno por anuncio tarda unos segundos en una Raspberry; con este, cargar el audio es cosa de décimas.
    Se maneja por IPC, de a un anuncio por vez (`lock`). Si se cae, el próximo `cargar` lo levanta."""

    def __init__(self, lanzar, mpv, args_mpv):
        self.lanzar, self.mpv, self.args = lanzar, mpv, list(args_mpv)
        self.lock = threading.Lock()
        self.proc = self.sock = self.dir = None
        self.buf = b""
        self.ids = 0

    def _vivo(self):
        return self.proc is not None and self.proc.poll() is None and self.sock is not None

    def asegurar(self, limite=15):
        """Lo levanta si no está (con el lock tomado, o desde el arranque)."""
        if self._vivo():
            return
        self.cerrar()
        self.dir = tempfile.mkdtemp(prefix="rockola-voz-")
        ruta = os.path.join(self.dir, "ipc")
        self.proc = self.lanzar([self.mpv, "--idle=yes", "--no-config", "--no-video", "--really-quiet", "--force-window=no",
                                 "--audio-display=no", "--ytdl=no", "--input-terminal=no", *self.args,
                                 f"--input-ipc-server={ruta}"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, preexec_fn=_morir_con_el_padre)
        fin = time.monotonic() + limite
        while True:
            if self.proc.poll() is not None:
                raise OSError("el mpv de voz se cerró al arrancar")
            try:
                c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                c.connect(ruta)
                break
            except OSError:
                c.close()
                if time.monotonic() > fin:
                    raise OSError("el mpv de voz no abrió el socket")
                time.sleep(0.05)
        c.settimeout(1)
        self.sock, self.buf = c, b""

    def cerrar(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None
        if self.dir:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None

    def _enviar(self, *comando):
        self.ids += 1
        self.sock.sendall(json.dumps({"command": list(comando), "request_id": self.ids}).encode() + b"\n")

    def _esperar(self, evento, limite):
        """Lee lo que manda mpv hasta que llega `evento` (con cuenta de lo que pasa en el medio: un end-file
        con error corta). Devuelve el mensaje."""
        fin = time.monotonic() + limite
        while True:
            while b"\n" in self.buf:
                linea, self.buf = self.buf.split(b"\n", 1)
                try:
                    m = json.loads(linea)
                except ValueError:
                    continue
                if m.get("event") == evento:
                    return m
                if m.get("event") == "end-file" and m.get("reason") in ("error", "unknown"):
                    raise OSError(f"mpv no pudo con el audio ({m.get('file_error') or m.get('reason')})")
            if time.monotonic() > fin:
                raise TimeoutError(evento)
            if self.proc.poll() is not None:
                raise OSError("el mpv de voz se cayó")
            try:
                trozo = self.sock.recv(65536)
            except TimeoutError:
                continue
            if not trozo:
                raise OSError("el mpv de voz cerró el socket")
            self.buf += trozo

    def cargar(self, ruta, limite=20):
        """Carga el audio y lo deja en pausa, listo para sonar. Con el lock tomado."""
        self.asegurar()
        self.sock.setblocking(False)   # (se tira lo que haya quedado de antes)
        try:
            while self.sock.recv(65536):
                pass
        except (BlockingIOError, OSError):
            pass
        self.sock.settimeout(1)
        self.buf = b""
        self._enviar("set_property", "pause", True)
        self._enviar("loadfile", str(ruta), "replace")
        self._esperar("file-loaded", limite)

    def sonar(self, limite=300):
        """Lo hace sonar y espera a que termine."""
        self._enviar("set_property", "pause", False)
        self._esperar("end-file", limite)

    def parar(self):
        try:
            self._enviar("stop")
        except (OSError, AttributeError):
            pass


class ErrorVoz(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


class Voz:
    """`bajar(on)`: baja/sube la música del parlante mientras suena la voz. `args_mpv`: las opciones de
    audio de la rockola. `lanzar`: cómo crear un proceso (ver reproductor._lanzar). `al_cambiar(sala)`:
    avisa a las páginas de esa sala. `principal`: el id de la sala del parlante."""

    def __init__(self, carpeta, bajar, lanzar, args_mpv=(), al_cambiar=lambda sala: None, mpv="mpv", principal="principal", volumen=lambda: 100):
        self.carpeta = Path(carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.bajar, self.lanzar, self.args_mpv, self.al_cambiar, self.mpv = bajar, lanzar, list(args_mpv), al_cambiar, mpv
        self.principal = principal
        self.volumen = volumen     # el volumen de la música del parlante (0-130): los anuncios suenan a ése, y con 0 no suenan
        self.pausadas = set()      # salas con los anuncios pausados (a mano; se pierde al reiniciar)
        self.lock = threading.RLock()
        self.anuncios = {}
        self.hablando = 0          # cuántas voces suenan en el parlante (para bajar la música una sola vez)
        self.mic = {}              # sala -> {"proc" (o None, sin parlante), "visto", "init", "pedazos", "seq", "cond"}
        self.canciones = {}        # sala -> canciones empezadas (para los "cada X canciones")
        self.suena = {}            # sala -> {"id", "desde", "dur"}
        self.pasado = {}           # anuncio -> el minuto ("AAAA-MM-DD HH:MM") en que ya sonó a horario (para que no suene dos veces)
        self.duracion_de = self._duracion   # (se cambia en las pruebas)
        for js in self.carpeta.glob("*.json"):
            try:
                a = json.loads(js.read_text())
                if ID.fullmatch(a["id"]) and (self.carpeta / a["archivo"]).is_file():
                    a.setdefault("sala", principal)   # (los de antes de que fueran por sala: los del parlante)
                    self.anuncios[a["id"]] = a
            except (OSError, ValueError, KeyError, TypeError):
                pass
        self.fijo = MpvFijo(lanzar, mpv, self.args_mpv)
        threading.Thread(target=self._calentar, daemon=True, name="voz-mpv").start()   # (así el primero ya sale rápido)
        threading.Thread(target=self._bucle, daemon=True, name="voz").start()

    # --- lo que ven las páginas

    def estado(self, sala):
        with self.lock:
            s = self.suena.get(sala)
            return {"mic": sala in self.mic, "suena": s["id"] if s else None, "pausa": sala in self.pausadas,
                    "silencio": self._en_silencio(sala),
                    "anuncios": sorted((dict(a, archivo=None) for a in self.anuncios.values()
                                       if a["sala"] == sala and not a.get("temporal")),
                                       key=lambda a: a["creado"])}

    def _en_silencio(self, sala):
        """¿La música del parlante está en 0? Entonces los anuncios del parlante tampoco suenan."""
        try:
            return sala == self.principal and not (self.volumen() or 0)
        except Exception:
            return False

    def pausar(self, sala, on):
        """Pausa (o reanuda) todos los anuncios de la sala; el micrófono y los que se tocan a mano no cambian."""
        with self.lock:
            (self.pausadas.add if on else self.pausadas.discard)(sala)
            for a in self.anuncios.values():   # (al volver, que los "cada X" cuenten desde ahora: no suenan todos juntos)
                if a["sala"] == sala:
                    a["ultimo"], a["desde"] = time.time(), self.canciones.get(sala, 0)
        self.al_cambiar(sala)

    def en_el_aire(self, sala):
        """Lo que suena ahora en la sala, para las páginas y la pantalla (sin PIN):
        {"mic": bool, "anuncio": nombre o None, "id": del anuncio, "t": segundos que lleva}."""
        with self.lock:
            s = self.suena.get(sala)
            a = self.anuncios.get(s["id"]) if s else None
            return {"mic": sala in self.mic, "anuncio": a["nombre"] if a else None, "id": a["id"] if a else None,
                    "t": round(time.monotonic() - s["desde"], 1) if a else 0}

    @staticmethod
    def tipo(ruta):
        """El tipo de audio de un archivo, mirando cómo empieza (los subidos se guardan como .audio, y sin
        tipo el navegador puede negarse a tocarlos). Si no lo reconoce, None."""
        try:
            with open(ruta, "rb") as f:
                h = f.read(16)
        except OSError:
            return None
        if h[:4] == b"\x1aE\xdf\xa3":
            return "audio/webm"
        if h[:4] == b"OggS":
            return "audio/ogg"
        if h[:3] == b"ID3" or h[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xe3"):
            return "audio/mpeg"
        if h[4:8] == b"ftyp":
            return "audio/mp4"
        if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
            return "audio/wav"
        if h[:4] == b"fLaC":
            return "audio/flac"
        if h[:2] == b"\xff\xf1" or h[:2] == b"\xff\xf9":
            return "audio/aac"
        return None

    def archivo(self, aid):
        """La ruta del audio de un anuncio (para que lo baje la radio), o None."""
        with self.lock:
            a = self.anuncios.get(str(aid))
            return self.carpeta / a["archivo"] if a else None

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

    def anuncio_nuevo(self, sala, data, nombre, cada_min, cada_canciones, por="", una_vez=False):
        """Guarda un anuncio. Con `una_vez` no se guarda: suena ya, una sola vez, y después se borra el audio."""
        if not data or len(data) > MAX_ANUNCIO:
            raise ErrorVoz(413, f"El audio tiene que pesar entre 1 byte y {MAX_ANUNCIO // 2**20} MB")
        with self.lock:
            if una_vez:
                if self._en_silencio(sala):
                    raise ErrorVoz(409, "La música está en silencio (volumen 0): los anuncios tampoco suenan")
                if sala in self.suena:
                    raise ErrorVoz(409, "Ya está sonando un anuncio")
                if sala in self.mic:
                    raise ErrorVoz(409, "El micrófono está abierto")
            elif sum(a["sala"] == sala and not a.get("temporal") for a in self.anuncios.values()) >= MAX_ANUNCIOS:
                raise ErrorVoz(429, f"Ya hay {MAX_ANUNCIOS} anuncios en esta sala: borrá alguno")
            aid = uuid.uuid4().hex[:10]
            archivo = f"{aid}.audio"
            (self.carpeta / archivo).write_bytes(data)
            a = {"id": aid, "sala": sala, "archivo": archivo, "nombre": " ".join(str(nombre or "").split())[:60] or "Anuncio",
                 "cada_min": self._num(cada_min, 24 * 60), "cada_canciones": self._num(cada_canciones, 200),
                 "activo": True, "azar": False, "programado": False, "horario": [], "creado": time.time(), "ultimo": time.time(),
                 "desde": self.canciones.get(sala, 0), "por": str(por)[:30]}
            if una_vez:   # (sin .json: no sobrevive a un reinicio, no aparece en la lista y nunca suena solo)
                a.update(temporal=True, activo=False, cada_min=0, cada_canciones=0)
                self.anuncios[aid] = a
                self.suena[sala] = {"id": aid, "desde": time.monotonic(), "dur": None}
                threading.Thread(target=self._sonar, args=(sala, a), daemon=True, name="anuncio").start()
            else:
                self.anuncios[aid] = a
                self._escribir(a)
        self.al_cambiar(sala)
        return a

    def anuncio_editar(self, sala, aid, cambios):
        with self.lock:
            a = self._de(sala, aid)
            if "nombre" in cambios:
                a["nombre"] = " ".join(str(cambios["nombre"]).split())[:60] or a["nombre"]
            if "cada_min" in cambios:
                a["cada_min"] = self._num(cambios["cada_min"], 24 * 60)
            if "cada_canciones" in cambios:
                a["cada_canciones"] = self._num(cambios["cada_canciones"], 200)
            if "activo" in cambios:
                a["activo"] = bool(cambios["activo"])
            if "horario" in cambios:
                a["horario"] = horarios(cambios["horario"])
            if "programado" in cambios:
                a["programado"] = bool(cambios["programado"])
                if a["programado"]:
                    a["azar"] = False
                    if not a.get("horario"):
                        a["horario"] = ["*:00"]   # (en punto, cada hora: se cambia después)
            if "azar" in cambios:
                a["azar"] = bool(cambios["azar"])
                if a["azar"]:
                    a["programado"] = False
                if a["azar"] and not a["cada_min"]:
                    a["cada_min"] = 30   # (en promedio, cada tanto: si no, nunca sonaría)
            a["ultimo"], a["desde"] = time.time(), self.canciones.get(sala, 0)   # (los "cada X" cuentan desde el cambio)
            self._escribir(a)
        self.al_cambiar(sala)

    def anuncio_borrar(self, sala, aid):
        with self.lock:
            a = self._de(sala, aid)
            del self.anuncios[aid]
            self.pasado.pop(aid, None)
            for f in (a["archivo"], f"{aid}.json"):
                (self.carpeta / f).unlink(missing_ok=True)
        self.al_cambiar(sala)

    def borrar_sala(self, sala):
        """Se borró la sala: se van sus anuncios y se corta su micrófono."""
        self.mic_cerrar(sala)
        with self.lock:
            for a in [a for a in self.anuncios.values() if a["sala"] == sala]:
                del self.anuncios[a["id"]]
                for f in (a["archivo"], f"{a['id']}.json"):
                    (self.carpeta / f).unlink(missing_ok=True)
            self.canciones.pop(sala, None)

    def _de(self, sala, aid):
        a = self.anuncios.get(str(aid))
        if a is None or a["sala"] != sala:
            raise ErrorVoz(404, "Ese anuncio ya no existe")
        return a

    def anuncio_sonar(self, sala, aid):
        """Lo hace sonar ya (para probarlo)."""
        with self.lock:
            a = self._de(sala, aid)
            if self._en_silencio(sala):
                raise ErrorVoz(409, "La música está en silencio (volumen 0): los anuncios tampoco suenan")
            if sala in self.suena:
                raise ErrorVoz(409, "Ya está sonando un anuncio")
            if sala in self.mic:
                raise ErrorVoz(409, "El micrófono está abierto")
            self.suena[sala] = {"id": a["id"], "desde": time.monotonic(), "dur": None}
        threading.Thread(target=self._sonar, args=(sala, a), daemon=True, name="anuncio").start()
        self.al_cambiar(sala)

    def cancion_nueva(self, sala):
        """Empezó una canción en la sala."""
        with self.lock:
            self.canciones[sala] = self.canciones.get(sala, 0) + 1

    def _duracion(self, ruta):
        """Cuánto dura un audio, en segundos (se lo pregunta a mpv); 30 si no se sabe."""
        try:
            r = subprocess.run([self.mpv, "--no-config", "--vo=null", "--ao=null", "--no-video", "--frames=1", "--really-quiet",
                                "--term-playing-msg=DUR=${=duration}", str(ruta)], capture_output=True, text=True, timeout=20)
            m = re.search(r"DUR=([\d.]+)", r.stdout)
            return max(1.0, min(float(m[1]), 300.0)) if m else 30.0
        except (OSError, ValueError, subprocess.SubprocessError):
            return 30.0

    def _calentar(self):
        with self.fijo.lock:
            try:
                self.fijo.asegurar()
            except Exception as e:
                print(f"  ⚠ no pude dejar levantado el mpv de los anuncios ({e!r}): se levanta uno por anuncio", flush=True)

    def _tocar_en_parlante(self, sala, a):
        """Lo toca en el parlante, con el mpv que queda levantado: se carga el audio (la música sigue igual),
        y recién cuando está listo baja la música y suena. Si ese mpv no anda, uno por anuncio."""
        with self.fijo.lock:
            try:
                self.fijo.cargar(self.carpeta / a["archivo"])
            except Exception as e:
                print(f"  ⚠ el mpv fijo de los anuncios no anduvo ({e!r}): lo paso con uno nuevo", flush=True)
                self.fijo.cerrar()
                return self._tocar_lanzando(sala, a)
            try:
                self.fijo._enviar("set_property", "volume", float(self.volumen() or 0))   # (suena a lo que está la música)
            except Exception:
                pass
            self._subir(+1)   # (cargado: ahora sí baja la música)
            try:
                with self.lock:
                    if sala in self.suena:
                        self.suena[sala]["desde"] = time.monotonic()   # (la radio se alinea con cuando suena de verdad)
                self.fijo.sonar()
            except Exception as e:
                print(f"  ⚠ no pude pasar el anuncio: {e!r}", flush=True)
                self.fijo.cerrar()   # (si quedó a medias, mejor uno limpio)
            finally:
                self.fijo.parar()
                self._subir(-1)

    def _tocar_lanzando(self, sala, a):
        """Lo toca en el parlante. mpv tarda unos segundos en arrancar (en una Raspberry más), y la música no
        tiene que bajar durante ese rato: se lo levanta con el audio cargado y en pausa, y recién cuando está
        listo baja la música y suena (con `desde`, el momento en que suena de verdad, se alinea la radio).
        Si no se puede por IPC, se hace como antes: bajar la música y que mpv suene solo. Al terminar
        vuelve a subir la música."""
        ruta = str(self.carpeta / a["archivo"])
        base = [self.mpv, "--no-config", "--no-video", "--really-quiet", *self.args_mpv, f"--volume={float(self.volumen() or 0)}"]
        directorio = tempfile.mkdtemp(prefix="rockola-voz-")
        p = None
        try:
            sock = os.path.join(directorio, "ipc")
            p = self.lanzar([*base, "--pause", f"--input-ipc-server={sock}", ruta], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ipc = self._ipc_listo(p, sock)
            if ipc is None:
                raise OSError("mpv no quedó listo")
        except Exception as e:   # (sin IPC: el camino de siempre)
            print(f"  ⚠ el anuncio sin mpv pausado ({e!r}): lo paso de la forma simple", flush=True)
            if p and p.poll() is None:
                p.kill()
            shutil.rmtree(directorio, ignore_errors=True)
            self._subir(+1)
            try:
                p = self.lanzar([*base, ruta], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._esperar(p)
            finally:
                self._subir(-1)
            return
        self._subir(+1)   # (listo para sonar: ahora sí baja la música)
        try:
            try:
                ipc.sendall(b'{"command":["set_property","pause",false]}\n')
            except OSError:
                pass
            with self.lock:
                if sala in self.suena:
                    self.suena[sala]["desde"] = time.monotonic()
            self._esperar(p)
        finally:
            try:
                ipc.close()
            except OSError:
                pass
            if p.poll() is None:
                p.kill()
            shutil.rmtree(directorio, ignore_errors=True)
            self._subir(-1)

    @staticmethod
    def _esperar(p):
        try:
            p.wait(timeout=300)
        except subprocess.TimeoutExpired:
            p.kill()

    @staticmethod
    def _ipc_listo(p, sock, limite=20):
        """Se conecta al socket de ese mpv y espera a que tenga el audio cargado (ya sabe cuánto dura).
        Devuelve el socket, o None si mpv se cerró o no llegó a tiempo."""
        fin = time.monotonic() + limite
        c = None
        while time.monotonic() < fin:
            if p.poll() is not None:
                return None
            if c is None:
                try:
                    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    c.settimeout(2)
                    c.connect(sock)
                except OSError:
                    c.close() if c else None
                    c = None
                    time.sleep(0.05)
                    continue
            try:
                c.sendall(b'{"command":["get_property","duration"],"request_id":7}\n')
                datos = b""
                while b"\n" not in datos or b'"request_id":7' not in datos:
                    trozo = c.recv(4096)
                    if not trozo or time.monotonic() > fin:   # (mpv se cerró, o se acabó el tiempo)
                        return None
                    datos += trozo
                if b'"error":"success"' in datos:
                    return c
            except (OSError, ValueError):
                c.close()
                c = None
            time.sleep(0.05)
        if c:
            c.close()
        return None

    def _sonar(self, sala, a):
        parlante = sala == self.principal
        try:
            if parlante:
                self._tocar_en_parlante(sala, a)
            else:   # sin parlante: sólo lo oye la radio, que lo baja; acá se espera lo que dura
                dur = self.duracion_de(self.carpeta / a["archivo"])
                with self.lock:
                    if sala in self.suena:
                        self.suena[sala]["dur"] = dur
                time.sleep(dur + 1)
        except Exception as e:
            print(f"  ⚠ no pude pasar el anuncio: {e!r}", flush=True)
        finally:
            with self.lock:
                a["ultimo"], a["desde"] = time.time(), self.canciones.get(sala, 0)
                if a.get("temporal"):   # (de una sola vez: ya sonó, se va)
                    self.anuncios.pop(a["id"], None)
                    (self.carpeta / a["archivo"]).unlink(missing_ok=True)
                try:
                    if a["id"] in self.anuncios:
                        self._escribir(a)
                except OSError:
                    pass
                self.suena.pop(sala, None)
            self.al_cambiar(sala)

    def _subir(self, delta):
        """Cuenta las voces que suenan en el parlante: la música baja con la primera y sube con la última."""
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

    def mic_abrir(self, sala):
        with self.lock:
            if sala in self.mic:
                return
            if sala in self.suena:
                raise ErrorVoz(409, "Está sonando un anuncio")
            p = None
            if sala == self.principal:
                p = self.lanzar([self.mpv, "--no-config", "--no-video", "--really-quiet", "--profile=low-latency",
                                 "--cache=no", "--demuxer-readahead-secs=0", "--audio-buffer=0.1", *self.args_mpv, "-"],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.mic[sala] = {"proc": p, "visto": time.monotonic(), "init": None, "pedazos": [], "seq": 0,
                              "cond": threading.Condition(self.lock)}
        if sala == self.principal:
            self._subir(+1)
        self.al_cambiar(sala)

    def mic_pedazo(self, sala, data):
        with self.lock:
            m = self.mic.get(sala)
            if not m:
                raise ErrorVoz(409, "El micrófono no está abierto")
            m["visto"] = time.monotonic()
            # Para la radio: el primero (con el encabezado) se guarda aparte, y los últimos se recuerdan un rato.
            if m["init"] is None:
                m["init"] = data
            m["seq"] += 1
            m["pedazos"] = (m["pedazos"] + [(m["seq"], data)])[-MAX_PEDAZOS:]
            m["cond"].notify_all()
        if m["proc"]:
            try:
                m["proc"].stdin.write(data)
                m["proc"].stdin.flush()
            except (OSError, ValueError):
                self.mic_cerrar(sala)
                raise ErrorVoz(409, "El micrófono se cortó")

    def mic_leer(self, sala, desde, espera=8):
        """Para la radio: (el primer pedazo, los que vinieron después de `desde`, el último número), o None si
        el micrófono no está abierto. Espera hasta `espera` segundos a que llegue uno nuevo."""
        fin = time.monotonic() + espera
        with self.lock:
            while True:
                m = self.mic.get(sala)
                if not m:
                    return None
                nuevos = [(n, d) for n, d in m["pedazos"] if n > desde]
                if nuevos or time.monotonic() >= fin:
                    return m["init"], nuevos, m["seq"]
                m["cond"].wait(timeout=max(0.05, fin - time.monotonic()))

    def mic_cerrar(self, sala):
        with self.lock:
            m = self.mic.pop(sala, None)
            if m:
                m["cond"].notify_all()
        if not m:
            return
        if m["proc"]:
            try:
                m["proc"].stdin.close()
            except (OSError, ValueError):
                pass
            threading.Timer(2, lambda: m["proc"].poll() is None and m["proc"].kill()).start()
            self._subir(-1)
        self.al_cambiar(sala)

    # --- cuándo suena cada anuncio

    def _bucle(self):
        while True:
            time.sleep(PASO)
            with self.lock:
                vencidos = [s for s, m in self.mic.items() if time.monotonic() - m["visto"] > MIC_VENCE]
                toca = []
                for sala in {a["sala"] for a in self.anuncios.values()}:
                    if sala in self.suena or sala in self.mic:
                        continue   # (de a uno por sala)
                    if sala in self.pausadas or self._en_silencio(sala):
                        # (pausada, o sin volumen: no suena y no se acumula; los "cada X" cuentan desde que se calla)
                        for a in self.anuncios.values():
                            if a["sala"] == sala:
                                a["ultimo"], a["desde"] = time.time(), self.canciones.get(sala, 0)
                        continue
                    a = next((a for a in sorted(self.anuncios.values(), key=lambda a: a["ultimo"]) if a["sala"] == sala and self._toca(a)), None)
                    if a:
                        toca.append(a)
            for sala in vencidos:
                self.mic_cerrar(sala)
            for a in toca:
                try:
                    self.anuncio_sonar(a["sala"], a["id"])
                except ErrorVoz:
                    pass

    def _toca(self, a):
        if not a["activo"]:
            return False
        if a.get("programado"):
            # A horas fijas: suena una vez en cada minuto que está en el horario (la vuelta es de 2 s, así que
            # se anota el minuto). Si en ese minuto la sala estaba ocupada, se saltea hasta la próxima.
            ahora = time.localtime()
            minuto = time.strftime("%Y-%m-%d %H:%M", ahora)
            if self.pasado.get(a["id"]) == minuto or not any(
                    h in (f"{ahora.tm_hour:02d}:{ahora.tm_min:02d}", f"*:{ahora.tm_min:02d}") for h in a.get("horario", ())):
                return False
            self.pasado[a["id"]] = minuto
            return True
        if a.get("azar"):
            # Al azar: `cada_min` es cada cuánto suena EN PROMEDIO. Se tira una moneda en cada vuelta
            # (cada 2 s) con la chance justa para eso: puede sonar dos veces seguidas o tardar una hora.
            return bool(a["cada_min"]) and random.random() < PASO / (a["cada_min"] * 60)
        if a["cada_min"] and time.time() - a["ultimo"] >= a["cada_min"] * 60:
            return True
        return bool(a["cada_canciones"]) and self.canciones.get(a["sala"], 0) - a["desde"] >= a["cada_canciones"]
