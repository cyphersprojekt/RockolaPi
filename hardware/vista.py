"""Vistas previas del gabinete (PNG). Lo usa `gabinete.py --vista`.

Renderizador mínimo con z-buffer en numpy (matplotlib no ordena bien la profundidad
entre piezas). Necesita numpy y Pillow (viene con matplotlib).
"""
import math

import numpy as np


def _triangulos(m):
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3]
    t = np.asarray(mesh.tri_verts)
    v = np.column_stack([v[:, 0], -v[:, 2], v[:, 1]])  # (x, y arriba, z al frente) -> z arriba
    return v[t]


def render(partes, ruta, elev=20, azim=-60, ancho=1100, alto=820, titulo=""):
    from PIL import Image, ImageDraw, ImageFont

    tris, cols = [], []
    for m, color in partes:
        t = _triangulos(m)
        tris.append(t)
        cols.append(np.repeat(np.array(color, float)[None], len(t), axis=0))
    tris, cols = np.vstack(tris), np.vstack(cols)

    # Cámara ortográfica
    e, a = math.radians(elev), math.radians(azim)
    hacia_cam = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
    der = np.cross(-hacia_cam, [0, 0, 1.0]); der /= np.linalg.norm(der)
    arr = np.cross(der, -hacia_cam)
    P = np.stack([tris @ der, tris @ arr, tris @ hacia_cam], axis=-1)   # (n, 3, 3)

    # Sombreado: luz desde arriba a la izquierda de la cámara + ambiente
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    luz = hacia_cam + 0.6 * arr - 0.5 * der; luz /= np.linalg.norm(luz)
    k = 0.38 + 0.62 * np.abs(n @ luz)
    rgb = np.clip(cols * k[:, None], 0, 1)

    # Escalar a la imagen
    pts = P.reshape(-1, 3)
    lo, hi = pts[:, :2].min(0), pts[:, :2].max(0)
    margen = 50
    s = min((ancho - 2 * margen) / (hi[0] - lo[0]), (alto - 2 * margen - 30) / (hi[1] - lo[1]))
    cx = (ancho - s * (hi[0] - lo[0])) / 2 - s * lo[0]
    cy = (alto + 30 + s * (hi[1] - lo[1])) / 2 + s * lo[1]
    X = P[..., 0] * s + cx
    Y = cy - P[..., 1] * s
    Z = P[..., 2]

    zbuf = np.full((alto, ancho), -np.inf)
    img = np.ones((alto, ancho, 3))
    caras = np.full((alto, ancho), -1, dtype=np.int64)
    for i in range(len(tris)):
        x, y, z = X[i], Y[i], Z[i]
        x0, x1 = max(int(x.min()), 0), min(int(x.max()) + 1, ancho - 1)
        y0, y1 = max(int(y.min()), 0), min(int(y.max()) + 1, alto - 1)
        if x0 > x1 or y0 > y1:
            continue
        den = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(den) < 1e-9:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w0 = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / den
        w1 = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / den
        w2 = 1 - w0 - w1
        dentro = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not dentro.any():
            continue
        prof = w0 * z[0] + w1 * z[1] + w2 * z[2]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        gana = dentro & (prof > sub)
        sub[gana] = prof[gana]
        img[y0:y1 + 1, x0:x1 + 1][gana] = rgb[i]
        caras[y0:y1 + 1, x0:x1 + 1][gana] = i

    # Líneas: donde cambia mucho la profundidad o la orientación de la cara
    nn = np.zeros((alto, ancho, 3))
    hay = caras >= 0
    nn[hay] = n[caras[hay]]
    zb = np.where(hay, zbuf, zbuf[hay].min() - 50 if hay.any() else 0)
    borde = np.zeros((alto, ancho), bool)
    for dy, dx in ((0, 1), (1, 0)):
        a_ = slice(0, alto - dy), slice(0, ancho - dx)
        b_ = slice(dy, alto), slice(dx, ancho)
        algo = hay[a_] | hay[b_]                 # silueta: geometría contra fondo
        ambos = hay[a_] & hay[b_]
        salto = algo & (np.abs(zb[a_] - zb[b_]) > 2.0)
        quiebre = ambos & ((nn[a_] * nn[b_]).sum(-1) < 0.85)
        borde[a_] |= salto | quiebre
    img[borde] *= 0.25

    im = Image.fromarray((img * 255).astype(np.uint8))
    if titulo:
        d = ImageDraw.Draw(im)
        try:  # DejaVu viene con matplotlib y tiene tildes
            import matplotlib
            from pathlib import Path
            fuente = ImageFont.truetype(str(Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf"), 22)
        except Exception:
            fuente = ImageFont.load_default()
        d.text((ancho / 2, 22), titulo, fill=(30, 30, 30), font=fuente, anchor="mt")
    im.save(ruta)
    print(f"  vista: {ruta}")


def generar(piezas, salida):
    import gabinete as g
    from manifold3d import Manifold

    GRIS, NARANJA, AZUL, VERDE = (0.82, 0.82, 0.85), (0.96, 0.62, 0.28), (0.2, 0.26, 0.42), (0.5, 0.75, 0.5)
    cuerpo, tapa, soporte = piezas["cuerpo"], piezas["tapa"], piezas["soporte"]
    pantalla = g.caja(g.X_P0, g.X_P1, g.Y_P0, g.Y_P1, -g.FRENTE - g.PANTALLA_GROSOR, -g.FRENTE)
    # Bultos de ejemplo pegados atrás de la pantalla (Raspberry y controladora)
    placas = (g.caja(g.W / 2 - 60, g.W / 2 + 25, 45, 101, g.Z_RANURA - 20, g.Z_RANURA)
              + g.caja(g.W / 2 + 40, g.W / 2 + 95, 30, 90, g.Z_RANURA - 12, g.Z_RANURA))

    ROJO = (0.75, 0.2, 0.2)
    botones = None
    for x in g.X_BOTONES:   # botones de muestra (sólo para las imágenes)
        b = Manifold.cylinder(4, g.BOTON_DIAMETRO / 2 - 0.8).translate((x, g.Y_BOTON, 0))
        botones = b if botones is None else botones + b
    extra = [(botones, ROJO)] if botones is not None else []
    sube = 75
    render([(cuerpo, GRIS), *extra, (pantalla.translate((0, sube, 0)), AZUL),
            (placas.translate((0, sube, 0)), (0.3, 0.6, 0.35)),
            (tapa.translate((0, sube + g.PANTALLA_ALTO + 25, 0)), NARANJA)],
           salida / "vista_despiece.png", elev=22, azim=-62,
           titulo="Despiece: la pantalla entra por arriba y la tapa la traba")
    render([(cuerpo, GRIS), *extra, (tapa, NARANJA), (pantalla, AZUL)],
           salida / "vista_frente.png", elev=12, azim=-70, titulo="Armado, de frente")
    if g.X_BOTONES:   # de frente, sólo la lámina de adelante: el grabado se ve como hueco
        x0, x1 = g.X_BOTONES[0] - 16, g.X_BOTONES[-1] + 16
        lamina = cuerpo ^ g.caja(x0, x1, 0, g.Y_P0 + 9, -0.3, 0)
        render([(lamina, GRIS)], salida / "vista_botonera.png", elev=0, azim=-90, ancho=1100, alto=420,
               titulo="Botonera: volumen −, anterior, play/pausa, siguiente, volumen +")
    render([(cuerpo, GRIS), (tapa, NARANJA)], salida / "vista_atras.png", elev=20, azim=118,
           titulo="Atrás: orejitas para colgar y ventilación")
    render([(cuerpo, GRIS), (tapa, NARANJA), (pantalla, AZUL)], salida / "vista_abajo.png", elev=-40, azim=-115,
           titulo="Abajo: salida de cables y ventilación")

    if g.X_BOTONES:
        # Teclas: corte por la del medio, y despiece de la placa, pulsadores y capuchones.
        x = g.X_BOTONES[2]
        corte = lambda m: m ^ g.caja(x, x + 40, -5, 40, -45, 10)
        VERDE_P, NEGRO, AZUL_D = (0.5, 0.75, 0.5), (0.15, 0.15, 0.15), (0.2, 0.4, 0.85)
        render([(corte(cuerpo), GRIS), (corte(g.placa_botones()), VERDE_P), (corte(g.capuchon(x)), ROJO),
                (corte(g.pulsador(x)), NEGRO), (corte(g.conectores_dupont(x)), AZUL_D)],
               salida / "vista_teclas_corte.png", elev=10, azim=-150, ancho=1000, alto=760,
               titulo="Corte: frente, capuchón, pulsador, placa y cable dupont")
        xs = g.X_BOTONES
        sep = lambda m, dz: m.translate((0, 0, dz))
        render([(sep(g.unir(*[g.capuchon(x) for x in xs]), 30), ROJO),
                (sep(g.unir(*[g.pulsador(x) for x in xs]), 12), NEGRO),
                (g.placa_botones(), VERDE_P),
                (sep(g.unir(*[g.conectores_dupont(x) for x in xs]), -14), AZUL_D)],
               salida / "vista_teclas.png", elev=22, azim=-120, ancho=1100, alto=640,
               titulo="Teclas: capuchones, pulsadores 6x6, placa y cables dupont (sin soldar)")

    _, elev = g.soporte()
    poner = lambda m: g.colocar_en_soporte(m).translate((0, elev, 0))
    puestos = [(poner(botones), ROJO)] if botones is not None else []
    render([(soporte, VERDE), (poner(cuerpo + tapa), GRIS), *puestos, (poner(pantalla), AZUL)],
           salida / "vista_soporte.png", elev=14, azim=-40, titulo="Parada en el escritorio")
    render([(soporte, VERDE), (poner(cuerpo + tapa), GRIS), (poner(pantalla), AZUL)],
           salida / "vista_soporte_costado.png", elev=0, azim=0.01, titulo="De costado: 15° hacia atrás")
    render([(soporte, VERDE)], salida / "vista_soporte_solo.png", elev=38, azim=145,
           titulo="Soporte: cuna inclinada con túnel para los cables")
