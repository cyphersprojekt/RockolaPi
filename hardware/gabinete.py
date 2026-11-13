#!/usr/bin/env python3
"""Gabinete imprimible de la rockola: pantalla + Raspberry + controladora del display.

Piezas:
  cuerpo.stl         marco frontal con botonera, paredes y fondo, con orejitas para colgar.
                     Abierto arriba: la pantalla (con todo lo que tiene pegado atrás) entra
                     deslizándose desde arriba por una ranura y apoya en un estante.
  tapa.stl           cierra arriba y queda justo encima de la pantalla: no la deja salir.
  soporte.stl        (opcional) cuna para dejarlo parado en un escritorio, inclinado.
  placa_botones.stl  sostiene 5 pulsadores táctiles de 6x6 detrás de la botonera (se pega).
  capuchones.stl     las teclas que pasan por los agujeros y aprietan los pulsadores.
  prueba_ranura.stl  la parte de abajo del cuerpo, para probar el calce antes de imprimir todo.

Uso:
    python gabinete.py              genera los STL, ya orientados para imprimir
    python gabinete.py --vista      además genera las imágenes de docs/img (necesita matplotlib)

Requiere:  pip install manifold3d numpy   (y matplotlib para --vista)

Medidas en mm. Ejes: x hacia la derecha mirando la pantalla de frente, y hacia arriba,
z hacia el frente (la cara de la pantalla está en z = 0, el fondo en z = -PROFUNDIDAD).
"""
import argparse
import math
import struct
import sys
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, JoinType, Manifold, OpType, set_circular_segments

# ================================================================== AJUSTES
# Pantalla (mirándola de frente)
PANTALLA_ANCHO = 230.0
PANTALLA_ALTO = 150.0
PANTALLA_GROSOR = 5.0
BISEL_IZQ, BISEL_DER, BISEL_SUP, BISEL_INF = 3.5, 6.0, 3.5, 8.0

PROFUNDIDAD = 40.0       # grosor total de la caja

# Paredes
PARED = 2.4              # laterales, piso y techo (6 perímetros de 0.4 mm)
FONDO = 3.0              # pared trasera; las orejas salen de acá
FRENTE = 2.0             # marco frontal

# Tolerancias (si la pantalla entra muy justa o muy floja, tocá estas)
HOLGURA = 0.5            # entre el borde de la pantalla y las paredes (a cada lado y arriba)
HOLGURA_GROSOR = 0.6     # ranura = grosor de la pantalla + esto
HOLGURA_TAPA = 0.2       # entre la tapa y el cuerpo

# Ventana y ranura
MARGEN_VENTANA = 0.5     # la ventana es un poco más grande que el área visible
CHAFLAN = 1.0            # bisel a 45° en el borde de la ventana
RIEL = 2.5               # cuánto pisa el riel de atrás el borde de la pantalla
RIEL_ESPESOR = 2.0
HUECO_FLEX = None        # (centro_x, ancho): hueco en el piso si el flex de la pantalla sale por abajo

RADIO = 3.0              # esquinas redondeadas (vistas de frente)

# Orejitas para colgar (ojal tipo bocallave: entra la cabeza del tornillo y baja)
OREJAS_X = (0.18, 0.82)  # posición, como fracción del ancho
OREJA_ANCHO = 26.0
OREJA_BASE = 34.0
OREJA_ALTO = 20.0
OJAL_DIAMETRO = 9.0      # por donde pasa la cabeza del tornillo
OJAL_RANURA = 4.5        # por donde corre el cuerpo del tornillo
OJAL_RECORRIDO = 7.0

# Agujero de cables (abajo): 2 de alimentación + Ethernet. Pasa un conector RJ45.
CABLES_ANCHO = 34.0      # a lo ancho de la caja
CABLES_LARGO = 16.0      # de adelante hacia atrás
CABLES_X = None          # centro en x (None = al medio, o a la izquierda si hay botonera)
CABLES_Z = 27.0          # distancia del centro del agujero al frente

# Botonera debajo de la pantalla:  [ − ]  [ ⏮ ]  [ ⏯ ]  [ ⏭ ]  [ + ]
BOTONERA = True
BOTONERA_ALTO = 30.0       # franja extra debajo de la pantalla
BOTON_DIAMETRO = 12.0      # agujero: botón de panel con tuerca (medí el tuyo)
BOTON_SEPARACION = 26.0    # entre centros
BOTON_LUGAR = 17.0         # diámetro libre detrás del frente (tuerca y cuerpo del botón)
BOTON_LARGO = 25.0         # cuánto se mete el botón hacia adentro, con los terminales
ICONOS = True              # íconos grabados arriba de cada botón
ICONO_TAMANO = 7.0
ICONO_PROFUNDIDAD = 0.6

# Pulsadores táctiles de 6x6 mm, sin soldar: placa trasera que los sostiene + capuchones
# que pasan por los agujeros de 12 mm. Los cables dupont hembra van directo a las patas.
TECLA_ALTO = 5.0           # alto total del pulsador: cuerpo + botoncito (medí el tuyo: 4.3, 5, 7…)
TECLA_LADO = 6.0
TECLA_CUERPO_ALTO = 3.5
TECLA_PATAS = (6.5, 4.5)   # separación de las patas: entre lados opuestos, y a lo largo de un lado
TECLA_JUEGO = 0.4          # luz entre capuchón y pulsador en reposo (el pulsador se hunde ~0,25 mm)
CAPUCHON_SOBRESALE = 3.0   # cuánto sobresale del frente
CAPUCHON_HOLGURA = 0.3     # en el agujero (radio)
CAPUCHON_ESPIGA = 2.0
PLACA_ESPESOR = 5.0        # detrás de los pulsadores
DUPONT = 2.54              # lado de la carcasa de un cable dupont
DUPONT_LARGO = 14.0

# Ventilación (ranuras de 3 mm)
VENT_ANCHO = 3.0
VENT_PASO = 7.0

# Soporte de escritorio
SOPORTE_ANGULO = 15.0    # inclinación hacia atrás
SOPORTE_ANCHO = 150.0
SOPORTE_ELEVACION = 20.0 # altura extra para que los cables doblen al salir por abajo
SOPORTE_LABIO = 7.0      # altura del labio de adelante (no tapa la pantalla)
SOPORTE_RESPALDO = 30.0
SOPORTE_PARED = 3.0
SOPORTE_PISO = 4.0
SOPORTE_COLA = 45.0      # cuánto se extiende hacia atrás (estabilidad)
SOPORTE_HOLGURA = 0.5
SOPORTE_TUNEL = 50.0     # ancho del túnel para los cables
SOPORTE_ALTO_TUNEL = 25.0  # alto de la salida de cables por atrás

SEGMENTOS = 48           # resolución de las curvas

# ============================================================ medidas derivadas
D = PROFUNDIDAD
W = PANTALLA_ANCHO + 2 * HOLGURA + 2 * PARED              # ancho exterior
X_P0 = PARED + HOLGURA                                     # pantalla: borde izquierdo
X_P1 = X_P0 + PANTALLA_ANCHO
Y_P0 = PARED + (BOTONERA_ALTO if BOTONERA else 0)          # donde apoya la pantalla
Y_P1 = Y_P0 + PANTALLA_ALTO
H = Y_P1 + HOLGURA + PARED                                 # alto exterior (sin orejas)
Y_TECHO = H - PARED                                        # donde apoya la tapa
Z_RANURA = -FRENTE - PANTALLA_GROSOR - HOLGURA_GROSOR      # fondo de la ranura
Z_RIEL = Z_RANURA - RIEL_ESPESOR
Z_FONDO = -(D - FONDO)                                     # cara interna del fondo
VENT_X0, VENT_X1 = X_P0 + BISEL_IZQ - MARGEN_VENTANA, X_P1 - BISEL_DER + MARGEN_VENTANA
VENT_Y0, VENT_Y1 = Y_P0 + BISEL_INF - MARGEN_VENTANA, Y_P1 - BISEL_SUP + MARGEN_VENTANA
# Botonera: centrada debajo de la pantalla; los botones a media altura del lugar libre
# entre el piso y el estante donde apoya la pantalla.
ESTANTE_PROF = -FRENTE - Z_RIEL                            # el estante tapa la ranura y el riel
Y_BAJO_ESTANTE = Y_P0 - 2 - ESTANTE_PROF                   # su parte de abajo, pegada al frente
Y_BOTON = (PARED + Y_BAJO_ESTANTE) / 2
X_BOTONES = [W / 2 + k * BOTON_SEPARACION for k in (-2, -1, 0, 1, 2)] if BOTONERA else []
NOMBRES_BOTONES = ["vol_menos", "anterior", "play", "siguiente", "vol_mas"]
Y_ICONO = (Y_BOTON + BOTON_DIAMETRO / 2 + VENT_Y0) / 2
# Pila de la tecla, de adelante hacia atrás (z). El capuchón en reposo apoya su cono en el
# borde interno del agujero.
R_AGUJERO = BOTON_DIAMETRO / 2
R_CAPUCHON = R_AGUJERO - CAPUCHON_HOLGURA
R_PESTANA = R_AGUJERO + 1.5
Z_CONO0 = -FRENTE + CAPUCHON_HOLGURA                  # donde empieza el cono (en reposo)
Z_CONO1 = Z_CONO0 - (R_PESTANA - R_CAPUCHON)          # cono a 45°: se imprime sin soportes
Z_PESTANA = Z_CONO1 - 1.0
Z_ESPIGA = Z_PESTANA - CAPUCHON_ESPIGA                # punta de la espiga
Z_TECLA_ARRIBA = Z_ESPIGA - TECLA_JUEGO               # botoncito del pulsador
Z_TECLA_BASE = Z_TECLA_ARRIBA - TECLA_ALTO            # donde apoya el pulsador
Z_PLACA_FRENTE = Z_TECLA_BASE + 2.0                   # bolsillo de 2 mm
Z_PLACA_ATRAS = Z_TECLA_BASE - PLACA_ESPESOR
R_TUBO = R_PESTANA + 0.3

if CABLES_X is not None:
    CABLES_CX = CABLES_X
elif BOTONERA:   # al costado, para no pasar por encima de los botones
    CABLES_CX = PARED + 6 + CABLES_ANCHO / 2
else:
    CABLES_CX = W / 2

set_circular_segments(SEGMENTOS)


# ================================================================== utilidades

def caja(x0, x1, y0, y1, z0, z1):
    return Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def unir(*piezas):
    return Manifold.batch_boolean([p for p in piezas if p is not None], OpType.Add)


def restar(base, *cortes):
    return Manifold.batch_boolean([base, *[c for c in cortes if c is not None]], OpType.Subtract)


def contorno():
    """Silueta de la caja vista de frente, con esquinas redondeadas."""
    cs = CrossSection.square((W, H))
    if RADIO > 0:
        cs = cs.offset(-RADIO, JoinType.Round).offset(RADIO, JoinType.Round)
    return cs


def prisma(cs, z0, z1):
    return Manifold.extrude(cs, z1 - z0).translate((0, 0, z0))


def ventana(y_arriba):
    """Corte de la ventana, con chaflán a 45° hacia el frente."""
    r = caja(VENT_X0, VENT_X1, VENT_Y0, y_arriba, -CHAFLAN, -CHAFLAN + 0.01)
    afuera = caja(VENT_X0 - CHAFLAN, VENT_X1 + CHAFLAN, VENT_Y0 - CHAFLAN, y_arriba + CHAFLAN, 0, 1)
    tronco = Manifold.batch_hull([r, afuera])
    recto = caja(VENT_X0, VENT_X1, VENT_Y0, y_arriba, -FRENTE - 1, -CHAFLAN + 0.02)
    return unir(tronco, recto)


def ranuras(x0, x1, ancho, paso):
    """Posiciones x de ranuras de ventilación repartidas entre x0 y x1."""
    n = int((x1 - x0 - ancho) // paso) + 1
    total = (n - 1) * paso + ancho
    inicio = x0 + (x1 - x0 - total) / 2
    return [inicio + i * paso for i in range(n)]


def cilindro_y(cx, cz, r, y0, y1):
    """Cilindro con eje vertical (y)."""
    return Manifold.cylinder(y1 - y0, r).rotate((-90, 0, 0)).translate((cx, y0, cz))


def icono(nombre, cx, cy, s=ICONO_TAMANO):
    """Íconos simples (−  ⏮  ⏯  ⏭  +) como figuras 2D centradas en (cx, cy)."""
    t, b = s / 2, s * 0.18

    def rect(x0, y0, x1, y1):
        return CrossSection.square((x1 - x0, y1 - y0)).translate((cx + x0, cy + y0))

    def tri(*pts):
        # Los polígonos tienen que ir en sentido antihorario (si no, se toman como huecos).
        area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))
        if area < 0:
            pts = pts[::-1]
        return CrossSection([[(cx + x, cy + y) for x, y in pts]])

    partes = {
        "vol_menos": [rect(-t, -b / 2, t, b / 2)],
        "vol_mas": [rect(-t, -b / 2, t, b / 2), rect(-b / 2, -t, b / 2, t)],
        "anterior": [rect(-t, -t, -t + b, t), tri((t, -t), (t, t), (-t + b * 1.4, 0))],
        "siguiente": [rect(t - b, -t, t, t), tri((-t, -t), (-t, t), (t - b * 1.4, 0))],
        "play": [tri((-t * 1.3, -t), (-t * 1.3, t), (t * 0.15, 0)),
                 rect(t * 0.45, -t, t * 0.45 + b, t), rect(t * 0.95, -t, t * 0.95 + b, t)],
    }[nombre]
    return CrossSection.batch_boolean(partes, OpType.Add)


# ================================================================== piezas

def cuerpo():
    c = prisma(contorno(), -D, 0)
    c = restar(
        c,
        caja(PARED, W - PARED, PARED, H + 1, Z_FONDO, -FRENTE),     # interior (abierto arriba)
        caja(-1, W + 1, Y_TECHO, H + 1, Z_FONDO, 1),                 # arriba: va la tapa
        caja(-1, W + 1, VENT_Y1, H + 1, -FRENTE, 1),                 # franja de arriba del marco: va en la tapa
        ventana(H + 1),
    )
    # Rieles detrás de la pantalla (izquierda, derecha y abajo): forman la ranura.
    c = unir(
        c,
        caja(PARED, X_P0 + RIEL, PARED, Y_TECHO, Z_RIEL, Z_RANURA),
        caja(X_P1 - RIEL, W - PARED, PARED, Y_TECHO, Z_RIEL, Z_RANURA),
        caja(PARED, W - PARED, Y_P0 - 0.01, Y_P0 + RIEL, Z_RIEL, Z_RANURA),
    )
    if BOTONERA:
        # Estante donde apoya la pantalla, con la parte de abajo a 45° (sin soportes al imprimir).
        estante = Manifold.batch_hull([
            caja(PARED, W - PARED, Y_P0 - 2, Y_P0, Z_RIEL, -FRENTE),
            caja(PARED, W - PARED, Y_BAJO_ESTANTE, Y_P0 - 2, -FRENTE - 0.01, -FRENTE),
        ])
        c = unir(c, estante)
    if HUECO_FLEX:
        cx, ancho = HUECO_FLEX
        c = restar(c, caja(cx - ancho / 2, cx + ancho / 2, Y_P0 - 1.2, Y_P0 + RIEL + 0.1, Z_RIEL - 0.1, -FRENTE))

    # Agujero de cables (abajo), en forma de ranura redondeada.
    r = CABLES_LARGO / 2
    dx = max(0.0, CABLES_ANCHO / 2 - r)
    agujero = Manifold.batch_hull([cilindro_y(CABLES_CX - dx, -CABLES_Z, r, -1, PARED + 1),
                                   cilindro_y(CABLES_CX + dx, -CABLES_Z, r, -1, PARED + 1)])

    # Ventilación: abajo en los extremos (fuera del soporte), atrás arriba y abajo.
    z_v0, z_v1 = Z_RIEL - 3, Z_FONDO + 4
    fuera_soporte = (W - SOPORTE_ANCHO) / 2
    cortes = [agujero]
    for x in ranuras(10, fuera_soporte - 6, VENT_ANCHO, VENT_PASO) + \
             ranuras(W - fuera_soporte + 6, W - 10, VENT_ANCHO, VENT_PASO):
        if abs(x + VENT_ANCHO / 2 - CABLES_CX) < CABLES_ANCHO / 2 + 4:
            continue   # ahí está el agujero de cables
        cortes.append(caja(x, x + VENT_ANCHO, -1, PARED + 1, z_v1, z_v0))
    for x, nombre in zip(X_BOTONES, NOMBRES_BOTONES):
        cortes.append(Manifold.cylinder(FRENTE + 2, BOTON_DIAMETRO / 2).translate((x, Y_BOTON, -FRENTE - 1)))
        if ICONOS:
            cortes.append(Manifold.extrude(icono(nombre, x, Y_ICONO), ICONO_PROFUNDIDAD + 1)
                          .translate((0, 0, -ICONO_PROFUNDIDAD)))
    for y0, y1 in ((45, 70), (H - 45, H - 20)):
        for x in ranuras(35, W - 35, VENT_ANCHO, VENT_PASO * 1.5):
            cortes.append(caja(x, x + VENT_ANCHO, y0, y1, -D - 1, Z_FONDO + 1))
    c = restar(c, *cortes)

    # Orejitas, al ras del fondo (quedan contra la pared).
    orejas = []
    for f in OREJAS_X:
        cx = W * f
        base = CrossSection.square((OREJA_BASE, 8)).translate((cx - OREJA_BASE / 2, H - 8))
        punta = CrossSection.circle(OREJA_ANCHO / 2).translate((cx, H + OREJA_ALTO - OREJA_ANCHO / 2))
        oreja = Manifold.extrude(CrossSection.batch_hull([base, punta]), FONDO).translate((0, 0, -D))
        y_ojal = H + 1 + OJAL_DIAMETRO / 2
        ojal = unir(
            Manifold.cylinder(FONDO + 2, OJAL_DIAMETRO / 2).translate((cx, y_ojal, -D - 1)),
            Manifold.batch_hull([
                Manifold.cylinder(FONDO + 2, OJAL_RANURA / 2).translate((cx, y_ojal, -D - 1)),
                Manifold.cylinder(FONDO + 2, OJAL_RANURA / 2).translate((cx, y_ojal + OJAL_RECORRIDO, -D - 1)),
            ]),
        )
        orejas.append(restar(oreja, ojal))
    return unir(c, *orejas)


def tapa():
    t = unir(
        caja(0, W, Y_TECHO, H, Z_FONDO + HOLGURA_TAPA, 0),                  # techo
        caja(0, W, VENT_Y1, H, -FRENTE, 0),                                  # franja de arriba del marco
        # faldón: centra la tapa dentro del cuerpo (por detrás de los rieles)
        caja(PARED + HOLGURA_TAPA, PARED + HOLGURA_TAPA + 1.6, Y_TECHO - 6, Y_TECHO,
             Z_FONDO + HOLGURA_TAPA, Z_RIEL - HOLGURA_TAPA),
        caja(W - PARED - HOLGURA_TAPA - 1.6, W - PARED - HOLGURA_TAPA, Y_TECHO - 6, Y_TECHO,
             Z_FONDO + HOLGURA_TAPA, Z_RIEL - HOLGURA_TAPA),
        caja(PARED + HOLGURA_TAPA, W - PARED - HOLGURA_TAPA, Y_TECHO - 6, Y_TECHO,
             Z_FONDO + HOLGURA_TAPA, Z_FONDO + HOLGURA_TAPA + 1.6),
    )
    t = Manifold.batch_boolean([t, prisma(contorno(), -D - 1, 1)], OpType.Intersect)
    cortes = [ventana(VENT_Y1)]
    for x in ranuras(30, W - 30, VENT_ANCHO, VENT_PASO):
        cortes.append(caja(x, x + VENT_ANCHO, Y_TECHO - 1, H + 1, Z_FONDO + 6, Z_RIEL - 3))
    return restar(t, *cortes)


def capuchon(x, y=None):
    """Capuchón de una tecla, en su lugar (en reposo)."""
    y = Y_BOTON if y is None else y
    frente = Manifold.cylinder(CAPUCHON_SOBRESALE - Z_CONO0, R_CAPUCHON).translate((x, y, Z_CONO0))
    borde = Manifold.cylinder(0.5, R_CAPUCHON, R_CAPUCHON - 0.5).translate((x, y, CAPUCHON_SOBRESALE - 0.5))
    frente = restar(frente, caja(x - 9, x + 9, y - 9, y + 9, CAPUCHON_SOBRESALE - 0.5, 9))
    cono = Manifold.cylinder(Z_CONO0 - Z_CONO1, R_PESTANA, R_CAPUCHON).translate((x, y, Z_CONO1))
    pestana = Manifold.cylinder(Z_CONO1 - Z_PESTANA, R_PESTANA).translate((x, y, Z_PESTANA))
    espiga = Manifold.cylinder(Z_PESTANA - Z_ESPIGA, 2.0).translate((x, y, Z_ESPIGA))
    return unir(frente, borde, cono, pestana, espiga)


def pulsador(x, y=None, patas=True):
    """Un pulsador táctil de 6x6 (para verificar y para las imágenes)."""
    y = Y_BOTON if y is None else y
    l, (pa, pb) = TECLA_LADO / 2, TECLA_PATAS
    cuerpo_ = caja(x - l, x + l, y - l, y + l, Z_TECLA_BASE, Z_TECLA_BASE + TECLA_CUERPO_ALTO)
    boton = Manifold.cylinder(TECLA_ALTO - TECLA_CUERPO_ALTO, 1.75).translate((x, y, Z_TECLA_BASE + TECLA_CUERPO_ALTO))
    patas_ = [caja(x + sx * pa / 2 - 0.35, x + sx * pa / 2 + 0.35, y + sy * pb / 2 - 0.15, y + sy * pb / 2 + 0.15,
                  Z_TECLA_BASE - 3.5, Z_TECLA_BASE + 0.8) for sx in (-1, 1) for sy in (-1, 1)]
    return unir(cuerpo_, boton, *(patas_ if patas else []))


def conectores_dupont(x, y=None):
    """Los dos cables dupont de una tecla, en patas opuestas en diagonal."""
    y = Y_BOTON if y is None else y
    (pa, pb), d = TECLA_PATAS, DUPONT / 2
    return unir(*[caja(x + sx * pa / 2 - d, x + sx * pa / 2 + d, y + sx * pb / 2 - d, y + sx * pb / 2 + d,
                       Z_TECLA_BASE - DUPONT_LARGO, Z_TECLA_BASE - 0.1) for sx in (-1, 1)])


def placa_botones():
    """Placa que va pegada adentro, detrás de la botonera: sostiene los 5 pulsadores."""
    x0, x1 = X_BOTONES[0] - 13, X_BOTONES[-1] + 13
    y0, y1 = PARED + 0.1, Y_BOTON + (Y_BOTON - PARED - 0.1)       # apoya en el piso, centrada
    placa = caja(x0, x1, y0, y1, Z_PLACA_ATRAS, Z_PLACA_FRENTE)
    tubos, cortes = [], []
    (pa, pb), l = TECLA_PATAS, TECLA_LADO / 2 + 0.1
    for x in X_BOTONES:
        # Tubo: guía el capuchón y apoya en la cara interna del frente (ahí va el pegamento).
        tubo = Manifold.cylinder(-FRENTE - Z_PLACA_FRENTE, R_TUBO + 1.2).translate((x, Y_BOTON, Z_PLACA_FRENTE))
        tubos.append(tubo)
        cortes.append(Manifold.cylinder(-FRENTE - Z_PLACA_FRENTE + 1, R_TUBO).translate((x, Y_BOTON, Z_PLACA_FRENTE - 0.01)))
        # Bolsillo del pulsador
        cortes.append(caja(x - l, x + l, Y_BOTON - l, Y_BOTON + l, Z_TECLA_BASE, Z_PLACA_FRENTE + 1))
        # Una ranura por pata, abiertas atrás: ahí entran los cables dupont hasta la pata.
        # Entre las dos de cada lado queda un tramo de pared que no deja mover el pulsador.
        for sx in (-1, 1):
            for sy in (-1, 1):
                xc, yc = x + sx * pa / 2, Y_BOTON + sy * pb / 2
                cortes.append(caja(xc - 1.3, xc + 1.3, yc - 1.3, yc + 1.3, Z_PLACA_ATRAS - 1, Z_PLACA_FRENTE + 1))
    return restar(unir(placa, *tubos), *cortes)


def capuchones_para_imprimir(n=6):
    """n capuchones en fila (uno de repuesto), con el frente hacia abajo."""
    return unir(*[capuchon(i * 16.0, 0).rotate((180, 0, 0)) for i in range(n)])


def colocar_en_soporte(m):
    """Lleva algo del sistema de la caja al del soporte: inclinado hacia atrás y elevado."""
    return m.rotate((-SOPORTE_ANGULO, 0, 0)).translate((0, SOPORTE_ELEVACION, 0))


def soporte():
    c, T, F = SOPORTE_HOLGURA, SOPORTE_PARED, SOPORTE_PISO
    xs0, xs1 = W / 2 - SOPORTE_ANCHO / 2, W / 2 + SOPORTE_ANCHO / 2
    cuna = colocar_en_soporte(unir(
        caja(xs0, xs1, -F, 0, -D - c - T, c + T),                      # piso
        caja(xs0, xs1, -F, SOPORTE_LABIO, c, c + T),                   # labio de adelante
        caja(xs0, xs1, -F, SOPORTE_RESPALDO, -D - c - T, -D - c),      # respaldo
    ))
    # Relleno hasta la mesa + cola hacia atrás: casco convexo de la cuna y su sombra.
    v = cuna.to_mesh().vert_properties[:, :3]
    y_mesa = v[:, 1].min() - SOPORTE_ELEVACION
    z_atras = v[:, 2].min() - SOPORTE_COLA
    sombra = v.copy()
    sombra[:, 1] = y_mesa
    cola = np.array([[x, y, z_atras] for x in (xs0, xs1) for y in (y_mesa, y_mesa + 5)])
    s = Manifold.hull_points(np.vstack([v, sombra, cola]))

    lugar_caja = colocar_en_soporte(caja(xs0 - 1, xs1 + 1, 0, 500, -D - c, c))
    z_tunel = -CABLES_Z + CABLES_LARGO / 2 + 4
    tunel = colocar_en_soporte(caja(CABLES_CX - SOPORTE_TUNEL / 2, CABLES_CX + SOPORTE_TUNEL / 2,
                                    -500, 0.01, -500, z_tunel))
    # Detrás de la caja, el túnel sigue con altura constante hasta salir por atrás.
    z_atras_caja = -D * math.cos(math.radians(SOPORTE_ANGULO))
    # Techo a dos aguas (45°): se imprime sin soportes.
    salida = Manifold.batch_hull([
        caja(CABLES_CX - SOPORTE_TUNEL / 2, CABLES_CX + SOPORTE_TUNEL / 2,
             y_mesa - 1, y_mesa + SOPORTE_ALTO_TUNEL, -500, z_atras_caja),
        caja(CABLES_CX - 0.5, CABLES_CX + 0.5,
             y_mesa, y_mesa + SOPORTE_ALTO_TUNEL + SOPORTE_TUNEL / 2 - 0.5, -500, z_atras_caja),
    ])
    cortes = [lugar_caja]
    if CABLES_CX + CABLES_ANCHO / 2 > xs0 and CABLES_CX - CABLES_ANCHO / 2 < xs1:
        cortes += [tunel, salida]          # si no, los cables caen por afuera del soporte
    if X_BOTONES:
        # Labio sólo en las puntas, para no tapar los botones.
        g0 = max(xs0 + 8, min(X_BOTONES) - BOTON_LUGAR / 2 - 4)
        g1 = min(xs1 - 8, max(X_BOTONES) + BOTON_LUGAR / 2 + 4)
        cortes.append(colocar_en_soporte(caja(g0, g1, 0.01, 500, c - 0.01, c + T + 30)))
    s = restar(s, *cortes)
    return s.translate((0, -y_mesa, 0)), -y_mesa   # mesa en y = 0


# ================================================================== verificación

def verificar(cuerpo_, tapa_, soporte_, elev):
    """Chequea con las piezas reales que todo encaje. Devuelve True si está todo bien."""
    e = 0.02          # achicar un poquito para no contar caras que se tocan como choque
    tol = 0.5         # mm³: menos que esto es ruido numérico
    caja_ = cuerpo_ + tapa_

    def pantalla(dx=0.0, dy=0.0, dz=0.0, y_hasta=None):
        return caja(X_P0 + e + dx, X_P1 - e + dx, Y_P0 + e + dy, (y_hasta or Y_P1) - e + dy,
                    -FRENTE - PANTALLA_GROSOR + e + dz, -FRENTE - e + dz)

    def choca(a, b):
        return (a ^ b).volume() > tol

    pruebas = []

    def prueba(desc, ok):
        pruebas.append(ok)
        print(f"  {'✓' if ok else '✗'} {desc}")

    print("Verificando…")
    prueba("la pantalla entra en su lugar sin chocar", not choca(pantalla(), caja_))
    prueba("entra deslizándose desde arriba (sin la tapa)",
           not choca(pantalla(y_hasta=H + PANTALLA_ALTO + 20), cuerpo_))
    visible_ok = True
    for dx in (-HOLGURA, 0, HOLGURA):
        for dy in (0, HOLGURA):
            activa = caja(X_P0 + BISEL_IZQ + dx, X_P1 - BISEL_DER + dx,
                          Y_P0 + BISEL_INF + dy, Y_P1 - BISEL_SUP + dy, -FRENTE + e, 5)
            visible_ok &= not choca(activa, caja_)
    prueba("el marco no tapa el área visible (aunque la pantalla se corra)", visible_ok)
    prueba("con la tapa puesta no puede salir por arriba", choca(pantalla(dy=HOLGURA + 0.3), tapa_))
    prueba("no se va hacia atrás (rieles)", choca(pantalla(dz=-HOLGURA_GROSOR - 0.3), cuerpo_))
    prueba("no se va hacia adelante (marco)", choca(pantalla(dz=0.3), cuerpo_))
    prueba("no se va a los costados", choca(pantalla(dx=HOLGURA + 0.3), cuerpo_)
           and choca(pantalla(dx=-HOLGURA - 0.3), cuerpo_))
    prueba("la tapa no choca con el cuerpo", not choca(tapa_, cuerpo_))
    camino = Manifold.batch_boolean([tapa_.translate((0, k, 0)) for k in range(0, 61, 3)], OpType.Add)
    prueba("la tapa entra desde arriba sin chocar con nada (ni con las orejas)",
           not choca(camino, cuerpo_))

    if X_BOTONES:
        placa = placa_botones()
        caps = unir(*[capuchon(x) for x in X_BOTONES])
        teclas = unir(*[pulsador(x) for x in X_BOTONES])
        duponts = unir(*[conectores_dupont(x) for x in X_BOTONES])
        prueba("los capuchones pasan por los agujeros sin rozar", not choca(caps, cuerpo_))
        prueba("los capuchones no se salen para adelante (la pestaña frena)",
               choca(caps.translate((0, 0, 0.3)), cuerpo_))
        prueba("la placa de los botones entra sin chocar con el cuerpo", not choca(placa, cuerpo_))
        prueba("la placa apoya en el frente y en el piso (donde se pega)",
               placa.min_gap(cuerpo_, 1) < 0.15)
        prueba("los pulsadores entran en sus bolsillos", not choca(teclas, placa))
        quietos = all(choca(teclas.translate(d), placa) for d in ((0.4, 0, 0), (-0.4, 0, 0), (0, 0.4, 0), (0, -0.4, 0)))
        prueba("los pulsadores no se mueven de costado", quietos)
        prueba("los capuchones no tocan los pulsadores en reposo", not choca(caps, teclas))
        # Apretado a fondo: recorre el juego + lo que se hunde el pulsador (~0,25 mm).
        apretado = caps.translate((0, 0, -(TECLA_JUEGO + 0.25)))
        prueba(f"al apretar, nada frena el capuchón antes del pulsador (juego {TECLA_JUEGO} mm)",
               choca(apretado, teclas) and not choca(apretado, placa) and not choca(apretado, cuerpo_))
        sin_patas = unir(*[pulsador(x, patas=False) for x in X_BOTONES])
        prueba("los cables dupont entran hasta las patas sin chocar",
               not choca(duponts, placa) and not choca(duponts, cuerpo_) and not choca(duponts, sin_patas))
        atras = unir(*[Manifold.cylinder(BOTON_LARGO, BOTON_LUGAR / 2)
                       .translate((x, Y_BOTON, -FRENTE - BOTON_LARGO - e)) for x in X_BOTONES])
        prueba("detrás de cada botón hay lugar para la tuerca y el cuerpo",
               not choca(atras, caja_) and not choca(atras, pantalla()))
        adelante = unir(*[Manifold.cylinder(15, BOTON_DIAMETRO / 2 + 2).translate((x, Y_BOTON, 0.01))
                          for x in X_BOTONES])
        r_c = CABLES_LARGO / 2
        cables = caja(CABLES_CX - CABLES_ANCHO / 2, CABLES_CX + CABLES_ANCHO / 2, PARED, PARED + 30,
                      -CABLES_Z - r_c, -CABLES_Z + r_c)
        prueba("los cables suben sin pasar por los botones", not choca(cables, atras))

    puesta = colocar_en_soporte(caja_).translate((0, elev, 0))
    prueba("la caja entra en el soporte", not choca(puesta, soporte_))
    prueba("la caja apoya en el soporte (no queda en el aire)", puesta.min_gap(soporte_, 2) < 0.6)
    r = CABLES_LARGO / 2
    columna = colocar_en_soporte(caja(CABLES_CX - CABLES_ANCHO / 2, CABLES_CX + CABLES_ANCHO / 2, -60, 0,
                                      -CABLES_Z - r, -CABLES_Z + r)).translate((0, elev, 0))
    prueba("debajo del agujero de cables el soporte está libre", not choca(columna, soporte_))
    if X_BOTONES:
        prueba("en el soporte, los botones quedan libres",
               not choca(colocar_en_soporte(adelante).translate((0, elev, 0)), soporte_))

    # Estabilidad: centro de masa de todo (gabinete al 70 %, pantalla ~350 g, electrónica ~120 g).
    masas = [(caja_.volume() / 1000 * 1.24 * 0.7, (W / 2, H / 2, -D / 2)),
             (350, ((X_P0 + X_P1) / 2, (Y_P0 + Y_P1) / 2, -FRENTE - PANTALLA_GROSOR / 2)),
             (120, (W / 2, H / 2, (Z_RIEL + Z_FONDO) / 2))]
    total = sum(m for m, _ in masas)
    cm = np.sum([np.array(p) * m for m, p in masas], axis=0) / total
    ang = math.radians(-SOPORTE_ANGULO)
    z_cm = cm[1] * math.sin(ang) + cm[2] * math.cos(ang)
    _, _, z0, _, _, z1 = soporte_.bounding_box()
    margen = min(z_cm - z0, z1 - z_cm)
    prueba(f"parada en el soporte no se vuelca (margen {margen:.0f} mm)", margen > 15)
    return all(pruebas)


# ================================================================== exportar

def guardar_stl(m, ruta):
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3].astype(np.float32)
    t = np.asarray(mesh.tri_verts)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    datos = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", 9), ("attr", "<u2")])
    datos["n"] = n
    datos["v"] = np.hstack([a, b, c])
    with open(ruta, "wb") as f:
        f.write(b"Rockola - gabinete".ljust(80, b" "))
        f.write(struct.pack("<I", len(t)))
        f.write(datos.tobytes())


def para_imprimir(m, giro):
    """Rota para imprimir y apoya la pieza en z = 0, centrada en el origen."""
    m = m.rotate(giro)
    (x0, y0, z0, x1, y1, z1) = m.bounding_box()
    return m.translate((-(x0 + x1) / 2, -(y0 + y1) / 2, -z0))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vista", action="store_true", help="generar imágenes de vista previa")
    ap.add_argument("--salida", type=Path, default=Path(__file__).parent)
    args = ap.parse_args()

    piezas = {"cuerpo": cuerpo(), "tapa": tapa()}
    sop, elev = soporte()
    piezas["soporte"] = sop
    # La parte de abajo del cuerpo, hasta un poco más arriba de donde apoya la pantalla:
    # para probar el calce de la pantalla y de los botones antes de imprimir todo.
    piezas["prueba_ranura"] = piezas["cuerpo"] ^ caja(-1, W + 1, -1, Y_P0 + 15, -D - 1, 1)
    if BOTONERA:
        piezas["placa_botones"] = placa_botones()
        piezas["capuchones"] = capuchones_para_imprimir()
    for nombre, m in piezas.items():
        if m.status().name != "NoError" or m.is_empty():
            sys.exit(f"{nombre}: la geometría salió mal ({m.status()})")

    if not verificar(piezas["cuerpo"], piezas["tapa"], sop, elev):
        print("\n⚠ Algo no encaja: revisá los AJUSTES antes de imprimir.\n")
    print()

    giros = {
        "cuerpo": (90, 0, 0),    # parado, apoyado en el piso de la caja
        "tapa": (-90, 0, 0),     # boca abajo, apoyado en el techo
        "soporte": (90, 0, 0),   # apoyado en la base
        "prueba_ranura": (90, 0, 0),
        "placa_botones": (0, 0, 0),   # apoyada en la cara de atrás, los tubos para arriba
        "capuchones": (0, 0, 0),      # ya vienen con el frente para abajo
    }
    args.salida.mkdir(parents=True, exist_ok=True)
    for nombre, m in piezas.items():
        p = para_imprimir(m, giros[nombre])
        x0, y0, z0, x1, y1, z1 = p.bounding_box()
        guardar_stl(p, args.salida / f"{nombre}.stl")
        gramos = p.volume() / 1000 * 1.24
        print(f"  {nombre + '.stl':19} {x1 - x0:6.1f} x {y1 - y0:6.1f} x {z1 - z0:6.1f} mm"
              f"   ~{gramos:4.0f} g de PLA si fuera macizo")

    d = (OREJAS_X[1] - OREJAS_X[0]) * W
    print(f"\n  Para colgar: dos tornillos a {d:.1f} mm uno del otro, a la misma altura;"
          f" cabeza de menos de {OJAL_DIAMETRO:.0f} mm, cuerpo de hasta {OJAL_RANURA - 0.5:.1f} mm.")

    if args.vista:
        import vista
        imagenes = Path(__file__).parent.parent / "docs" / "img"
        imagenes.mkdir(parents=True, exist_ok=True)
        vista.generar(piezas, imagenes)


if __name__ == "__main__":
    main()
