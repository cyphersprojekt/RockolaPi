#!/usr/bin/env python3
"""Salidas de audio de PipeWire, a partir de `pw-dump` (JSON por stdin). Lo usan audio.sh y bluetooth.sh.

    pw-dump | python3 salidas.py listar       lista numerada (* = la que se usa)
    pw-dump | python3 salidas.py id N         id de PipeWire de la salida número N
    pw-dump | python3 salidas.py bt MAC       id de la salida de ese parlante Bluetooth
    pw-dump | python3 salidas.py nodo NOMBRE  id de la salida con ese node.name (p. ej. la de «todos los parlantes»)
"""
import json
import sys

objs = json.load(sys.stdin)

actual = None
for o in objs:
    if o.get("type") == "PipeWire:Interface:Metadata" and (o.get("props") or {}).get("metadata.name") == "default":
        for m in o.get("metadata") or []:
            if m.get("key") == "default.audio.sink" and isinstance(m.get("value"), dict):
                actual = m["value"].get("name")

salidas = []  # (id, node.name, descripción)
for o in objs:
    if o.get("type") != "PipeWire:Interface:Node":
        continue
    p = (o.get("info") or {}).get("props") or {}
    if p.get("media.class") == "Audio/Sink":
        nombre = p.get("node.name", "")
        salidas.append((o["id"], nombre, p.get("node.description") or p.get("node.nick") or nombre))

modo = sys.argv[1] if len(sys.argv) > 1 else "listar"
if modo == "listar":
    if not salidas:
        print("  (no encontré salidas de audio: ¿está corriendo PipeWire?)")
    for n, (_, nombre, desc) in enumerate(salidas, 1):
        print(f"  {'*' if nombre == actual else ' '} {n}. {desc}")
elif modo == "id":
    n = int(sys.argv[2])
    print(salidas[n - 1][0] if 1 <= n <= len(salidas) else "")
elif modo == "bt":
    mac = sys.argv[2].replace(":", "_").upper()
    print(next((i for i, nombre, _ in salidas if nombre.upper().startswith("BLUEZ_OUTPUT." + mac)), ""))
elif modo == "nodo":
    print(next((i for i, nombre, _ in salidas if nombre == sys.argv[2]), ""))
