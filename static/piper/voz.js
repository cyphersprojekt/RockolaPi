// Texto a voz en el navegador con Piper (la voz «Daniela», es_AR). Corre acá, en la compu de quien lo
// usa: la Raspberry no gasta nada, sólo recibe el WAV ya hecho (como cualquier anuncio subido).
//
// Basado en @mintplex-labs/piper-tts-web (MIT), que a su vez sale de @diffusionstudio/vits-web. Lo que
// se baja, y de dónde (se guarda en el navegador: la primera vez tarda, las siguientes no):
//   · la voz (~114 MB):          HuggingFace (rhasspy/piper-voices)
//   · el motor ONNX (wasm):      cdn.jsdelivr.net y cdnjs.cloudflare.com (onnxruntime-web 1.18.0)
//   · el fonemizador (espeak):   cdn.jsdelivr.net (@diffusionstudio/piper-wasm), y fonemas.js, de acá
import { createPiperPhonemize } from "./fonemas.js";

const HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/";
// Las voces en español de Piper. `mb`: lo que pesa (se baja una vez y queda en el navegador).
export const VOCES = [
  { id: "daniela", nombre: "Daniela · Argentina", ruta: "es_AR/daniela/high/es_AR-daniela-high", mb: 114 },
  { id: "claude", nombre: "Claude · México", ruta: "es_MX/claude/high/es_MX-claude-high", mb: 63 },
  { id: "ald", nombre: "Ald · México", ruta: "es_MX/ald/medium/es_MX-ald-medium", mb: 63 },
  { id: "davefx", nombre: "Davefx · España", ruta: "es_ES/davefx/medium/es_ES-davefx-medium", mb: 63 },
  { id: "sharvard", nombre: "Sharvard · España", ruta: "es_ES/sharvard/medium/es_ES-sharvard-medium", mb: 77, hablantes: 2 },
  { id: "carlfm", nombre: "Carlfm · España (calidad baja)", ruta: "es_ES/carlfm/x_low/es_ES-carlfm-x_low", mb: 28 },
];
// Los ajustes y sus valores de fábrica. variacion/ritmo: null = los de la voz.
export const AJUSTES = { voz: "daniela", hablante: 0, velocidad: 1, volumen: 100, normalizar: true,
                         variacion: null, ritmo: null, pausa: 0.3, inicio: 0.2, final: 0.2 };
const ORT_JS = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.18.0/dist/esm/ort.wasm.min.js";
const ORT_WASM = "https://cdnjs.cloudflare.com/ajax/libs/onnxruntime-web/1.18.0/";
const FONEMAS = "https://cdn.jsdelivr.net/npm/@diffusionstudio/piper-wasm@1.0.0/build/piper_phonemize";
const CACHE = "rockola-piper";
const MAX_TROZO = 400;   // letras por vez (en frases largas se parte por las oraciones)

let sesion = null;   // { id, ort, modelo, config }: la de la voz de ahora (al cambiar de voz se arma otra)

// Un archivo de internet, guardado en el navegador (Cache API). `avance(cargado, total)` mientras baja.
async function traer(url, avance) {
  let cache = null;
  try { cache = await caches.open(CACHE); } catch { /* sin caché (modo privado): se baja cada vez */ }
  const guardado = cache && await cache.match(url);
  if (guardado) return guardado.arrayBuffer();
  const r = await fetch(url);
  if (!r.ok) throw new Error(`No pude bajar ${url.split("/").pop()} (${r.status})`);
  const total = +r.headers.get("Content-Length") || 0, trozos = [];
  let hecho = 0;
  const lector = r.body.getReader();
  for (;;) {
    const { done, value } = await lector.read();
    if (done) break;
    trozos.push(value); hecho += value.length;
    avance?.(hecho, total);
  }
  const blob = new Blob(trozos);
  if (cache) { try { await cache.put(url, new Response(blob)); } catch { /* sin lugar: no se guarda */ } }
  return blob.arrayBuffer();
}

async function preparar(voz, avance) {
  if (sesion?.id === voz.id) return sesion;
  sesion = null;   // (una sola en memoria: cada voz pesa)
  const url = HF + voz.ruta + ".onnx";
  const ort = (await import(ORT_JS)).default || (await import(ORT_JS));
  ort.env.wasm.wasmPaths = ORT_WASM;
  ort.env.wasm.numThreads = self.crossOriginIsolated ? Math.min(4, navigator.hardwareConcurrency || 1) : 1;
  ort.env.wasm.proxy = true;   // en un worker: la página no se traba mientras genera
  const config = JSON.parse(new TextDecoder().decode(await traer(url + ".json")));
  const modelo = await ort.InferenceSession.create(await traer(url, avance), { executionProviders: ["wasm"] });
  return sesion = { id: voz.id, ort, modelo, config };
}

// Las frases de a trozos de hasta MAX_TROZO letras.
function partir(texto) {
  const oraciones = texto.replace(/\s+/g, " ").trim().match(/[^.!?¡¿]+[.!?]*|[.!?]+/g) || [];
  const trozos = [];
  let actual = "";
  for (const o of oraciones) {
    if (actual && (actual + o).length > MAX_TROZO) { trozos.push(actual.trim()); actual = ""; }
    actual += o;
    while (actual.length > MAX_TROZO) { trozos.push(actual.slice(0, MAX_TROZO).trim()); actual = actual.slice(MAX_TROZO); }
  }
  if (actual.trim()) trozos.push(actual.trim());
  return trozos.filter(t => /[\p{L}\p{N}]/u.test(t));
}

async function fonemas(texto, config) {
  return new Promise(async (resolver, rechazar) => {
    const modulo = await createPiperPhonemize({
      print: dato => resolver(JSON.parse(dato).phoneme_ids),
      printErr: msg => rechazar(new Error(msg)),
      locateFile: url => url.endsWith(".wasm") ? `${FONEMAS}.wasm` : url.endsWith(".data") ? `${FONEMAS}.data` : url,
    });
    modulo.callMain(["-l", config.espeak.voice, "--input", JSON.stringify([{ text: texto }]), "--espeak_data", "/espeak-ng-data"]);
  });
}

function aWav(pcm, tasa) {
  const buf = new ArrayBuffer(44 + pcm.length * 2), v = new DataView(buf);
  const txt = (p, s) => { for (let i = 0; i < s.length; i++) v.setUint8(p + i, s.charCodeAt(i)); };
  txt(0, "RIFF"); v.setUint32(4, 36 + pcm.length * 2, true); txt(8, "WAVEfmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, tasa, true); v.setUint32(28, tasa * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  txt(36, "data"); v.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 32767 | 0, true);
  return new Blob([buf], { type: "audio/wav" });
}

// ¿Ya está bajada (en el navegador) esta voz? Para avisar que la primera vez baja.
export async function estaBajada(id) {
  const voz = VOCES.find(v => v.id === id);
  try { return !!voz && !!(await (await caches.open(CACHE)).match(HF + voz.ruta + ".onnx")); } catch { return false; }
}

const num = (x, def, min, max) => Number.isFinite(+x) && x !== null && x !== "" ? Math.min(max, Math.max(min, +x)) : def;

// El texto, tal cual, dicho por la voz: devuelve un WAV (Blob). `aj`: los ajustes (ver AJUSTES);
// `estado(texto)` cuenta cómo va.
export async function hablar(texto, aj = {}, estado = () => {}) {
  aj = { ...AJUSTES, ...aj };
  const voz = VOCES.find(v => v.id === aj.voz) || VOCES[0];
  const trozos = partir(texto);
  if (!trozos.length) throw new Error("No hay nada para decir");
  estado(`Preparando la voz… (si es la primera vez baja ${voz.mb} MB)`);
  const { ort, modelo, config } = await preparar(voz, (h, t) => estado(`Bajando la voz… ${t ? Math.round(100 * h / t) + " %" : Math.round(h / 1048576) + " MB"}`));
  const tasa = config.audio.sample_rate, ini = config.inference;
  const escalas = [num(aj.variacion, ini.noise_scale, 0, 1.5),
                   num(aj.velocidad, 1, 0.4, 2) && ini.length_scale / num(aj.velocidad, 1, 0.4, 2),   // más rápida = menos largo
                   num(aj.ritmo, ini.noise_w, 0, 1.5)];
  const sid = voz.hablantes ? Math.min(voz.hablantes - 1, Math.max(0, aj.hablante | 0)) : null;
  const pcms = [], silencio = seg => new Float32Array(Math.round(num(seg, 0, 0, 5) * tasa));
  pcms.push(silencio(aj.inicio));
  for (const [i, t] of trozos.entries()) {
    estado(trozos.length > 1 ? `Generando… ${i + 1} de ${trozos.length}` : "Generando…");
    const ids = await fonemas(t, config);
    const feeds = { input: new ort.Tensor("int64", ids, [1, ids.length]),
                    input_lengths: new ort.Tensor("int64", [ids.length]),
                    scales: new ort.Tensor("float32", escalas) };
    if (sid !== null) feeds.sid = new ort.Tensor("int64", [sid]);
    pcms.push((await modelo.run(feeds)).output.data);
    if (i < trozos.length - 1) pcms.push(silencio(aj.pausa));
  }
  pcms.push(silencio(aj.final));
  const todo = new Float32Array(pcms.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of pcms) { todo.set(p, o); o += p.length; }
  // Volumen: con «normalizar», primero se sube hasta el tope (sin pasarse); después, el % elegido (100 = ese tope).
  let pico = 0;
  for (let k = 0; k < todo.length; k++) pico = Math.max(pico, Math.abs(todo[k]));
  const ganancia = (aj.normalizar && pico > 0 ? 0.89 / pico : 1) * num(aj.volumen, 100, 0, 300) / 100;   // 0.89 ≈ -1 dB
  for (let k = 0; k < todo.length; k++) todo[k] *= ganancia;   // (aWav recorta lo que pase de ±1)
  return aWav(todo, tasa);
}
