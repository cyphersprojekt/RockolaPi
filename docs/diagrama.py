#!/usr/bin/env python3
"""Genera el diagrama de conexión de los botones (docs/img/conexiones.png y .svg).

    python docs/diagrama.py      (necesita matplotlib)

Los pines de señal salen de controles.py; si los cambiás ahí, el diagrama se actualiza.
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI.parent))
from controles import BOTONES, POR_DEFECTO  # noqa: E402

# Numeración BCM -> pin físico del conector de 40 pines
BCM_A_FISICO = {2: 3, 3: 5, 4: 7, 17: 11, 27: 13, 22: 15, 10: 19, 9: 21, 11: 23, 5: 29, 6: 31,
                13: 33, 19: 35, 26: 37, 14: 8, 15: 10, 18: 12, 23: 16, 24: 18, 25: 22, 8: 24,
                7: 26, 12: 32, 16: 36, 20: 38, 21: 40}
TIERRAS = {6, 9, 14, 20, 25, 30, 34, 39}
# Tierra de cada botón: una para cada uno, pegada a su pin de señal (no hace falta empalmar).
TIERRA_DE = {"vol_menos": 20, "anterior": 25, "play": 30, "siguiente": 34, "vol_mas": 39}
NOMBRE = {"vol_menos": "Volumen −", "anterior": "Anterior", "play": "Play / Pausa",
          "siguiente": "Siguiente", "vol_mas": "Volumen +"}
COLOR = {"vol_menos": "#2f6fd6", "anterior": "#d9a400", "play": "#2e9e44",
         "siguiente": "#e8731c", "vol_mas": "#b03ab5"}

PASO = 0.62
X0, Y_IMPAR, Y_PAR = 0.9, 7.0, 7.62


def xy_pin(n):
    return X0 + ((n - 1) // 2) * PASO, (Y_IMPAR if n % 2 else Y_PAR)


def columna(n):
    return (n - 1) // 2


def main():
    senal = {b: BCM_A_FISICO[POR_DEFECTO[b]] for b in BOTONES}
    for b in BOTONES:   # cada tierra tiene que estar pegada a su señal (misma columna o la de al lado)
        assert TIERRA_DE[b] in TIERRAS and abs(columna(TIERRA_DE[b]) - columna(senal[b])) <= 1, b
    de_quien = {**{p: (b, "señal") for b, p in senal.items()}, **{p: (b, "tierra") for b, p in TIERRA_DE.items()}}

    fig, ax = plt.subplots(figsize=(14, 11), dpi=130)
    ax.set_xlim(-0.6, 13.8); ax.set_ylim(-0.4, 10.3); ax.set_aspect("equal"); ax.axis("off")

    # --- 1. El conector, con cada par del color de su botón
    ax.add_patch(FancyBboxPatch((0.15, 5.75), 13.3, 3.25, boxstyle="round,pad=0.05,rounding_size=0.3",
                                fc="#1f7a3a", ec="#14532a", lw=1.5, alpha=0.92))
    ax.text(6.8, 8.62, "Raspberry Pi vista desde arriba: el conector de 40 pines", color="white",
            fontsize=12.5, weight="bold", ha="center")
    ax.text(0.35, 6.0, "← lado de la microSD", color="#d8f0de", fontsize=9.5)
    ax.text(13.25, 6.0, "lado de los USB y el Ethernet →", color="#d8f0de", fontsize=9.5, ha="right")
    ax.add_patch(Rectangle((X0 - 0.38, Y_IMPAR - 0.36), 19 * PASO + 0.76, (Y_PAR - Y_IMPAR) + 0.72,
                           fc="#202020", ec="black"))
    for n in range(1, 41):
        x, y = xy_pin(n)
        if n in de_quien:
            b, rol = de_quien[n]
            fc, ec, lw, txt = (COLOR[b], "white", 1.4, "white") if rol == "señal" else ("black", COLOR[b], 3.2, "white")
        else:
            fc, ec, lw, txt = ("#7a7a7a" if n in TIERRAS else "#c9a227"), "#555", 1, "black"
        forma = (Rectangle((x - 0.22, y - 0.22), 0.44, 0.44, fc=fc, ec="white", lw=1.4) if n == 1
                 else Circle((x, y), 0.23, fc=fc, ec=ec, lw=lw))
        ax.add_patch(forma)
        ax.text(x, y, str(n), ha="center", va="center", fontsize=6.6, color=txt, weight="bold")
    # nombre de cada botón debajo de su par, en dos alturas para que no se pisen
    for k, b in enumerate(BOTONES):
        xs = [xy_pin(senal[b])[0], xy_pin(TIERRA_DE[b])[0]]
        xm = sum(xs) / 2
        y_txt = 5.25 - 0.5 * (k % 2)
        ax.plot([xm, xm], [Y_IMPAR - 0.4, y_txt + 0.22], color=COLOR[b], lw=1.6)
        ax.text(xm, y_txt, NOMBRE[b], ha="center", va="top", fontsize=10.5, weight="bold", color=COLOR[b])
    ax.annotate("pin 1: el cuadrado", xy=xy_pin(1), xytext=(0.2, 9.9), fontsize=9,
                arrowprops=dict(arrowstyle="->", color="#333"))
    # leyenda
    ax.add_patch(Circle((6.3, 10.0), 0.2, fc="#555", ec="white", lw=1.4))
    ax.text(6.6, 10.0, "de color: señal del botón", va="center", fontsize=10)
    ax.add_patch(Circle((9.7, 10.0), 0.2, fc="black", ec="#555", lw=3))
    ax.text(10.0, 10.0, "negro con borde de color: su tierra", va="center", fontsize=10)

    # --- 2. Un pulsador visto desde atrás: los dos cables en patas en diagonal
    cx, cy, l = 2.4, 2.1, 1.1
    ax.text(cx, 4.05, "Cada pulsador, visto desde atrás", ha="center", fontsize=11.5, weight="bold")
    ax.add_patch(Rectangle((cx - l, cy - l), 2 * l, 2 * l, fc="#2b2b2b", ec="black", lw=1.5))
    ax.add_patch(Circle((cx, cy), 0.45, fc="#444", ec="#111"))
    patas = {(-1, 1): None, (1, 1): "tierra", (-1, -1): "señal", (1, -1): None}
    for (sx, sy), rol in patas.items():
        px, py = cx + sx * (l + 0.18), cy + sy * 0.75
        ax.add_patch(Rectangle((px - 0.09, py - 0.16), 0.18, 0.32, fc="#bbb", ec="#666"))
        if rol:
            color = "#2f6fd6" if rol == "señal" else "black"
            ax.add_patch(Rectangle((px - 0.27, py - 0.27), 0.54, 0.54, fc=color, ec="black", alpha=0.85))
            ax.plot([px, px + sx * 1.3], [py, py], color=color, lw=3)
            ax.text(px + sx * 1.38, py, f"{rol}\n(dupont)", ha="left" if sx > 0 else "right", va="center",
                    fontsize=9.5, color=color, weight="bold")
    ax.text(cx, -0.05, "Usá dos patas en diagonal: siempre quedan una de cada lado del contacto.\n"
                       "Si el botón queda \"siempre apretado\", pasá un cable a la otra pata del mismo costado.",
            ha="center", fontsize=8.8, color="#333")

    # --- 3. Tabla
    x_t, y_t = 6.6, 4.05
    ax.text(x_t, y_t, "Botón", fontsize=11, weight="bold")
    ax.text(x_t + 2.6, y_t, "Señal", fontsize=11, weight="bold")
    ax.text(x_t + 5.2, y_t, "Tierra", fontsize=11, weight="bold")
    for k, b in enumerate(BOTONES):
        y = y_t - 0.62 * (k + 1)
        ax.add_patch(Circle((x_t - 0.25, y + 0.1), 0.14, fc=COLOR[b], ec="none"))
        ax.text(x_t, y, NOMBRE[b], fontsize=10.5, color=COLOR[b], weight="bold")
        ax.text(x_t + 2.6, y, f"pin {senal[b]}  (GPIO{POR_DEFECTO[b]})", fontsize=10.5)
        ax.text(x_t + 5.2, y, f"pin {TIERRA_DE[b]}", fontsize=10.5)
    ax.text(x_t, y_t - 0.62 * 6 - 0.05,
            "10 cables dupont hembra-hembra. Sin resistencias:\nla Raspberry usa las suyas (pull-up).",
            fontsize=9.5, color="#333", va="top")

    fig.suptitle("Conexión de los botones de la rockola (sin soldar)", fontsize=16, weight="bold", y=0.93)
    salida = AQUI / "img"
    salida.mkdir(exist_ok=True)
    for ext in ("png", "svg"):
        fig.savefig(salida / f"conexiones.{ext}", bbox_inches="tight", facecolor="white")
    print("listo:", salida / "conexiones.png", "y .svg")


if __name__ == "__main__":
    main()
