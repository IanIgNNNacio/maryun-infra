#!/usr/bin/env python3
"""Rota una clave de API de Metabase.

La vieja entra por la entrada estandar. Imprime SOLO la nueva, en una linea, y
nada mas: esta pensado para redirigir su salida a un archivo, no para leerla.

Motivo de existir: la clave vieja se filtro en un mensaje de error -urllib
imprime el valor de la cabecera cuando la rechaza, y un \r de Windows la hizo
invalida-. Una clave de administrador de un Metabase publico no se queda.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://10.8.0.1:3000"
vieja = sys.stdin.read().strip()


def api(ruta, metodo="GET", cuerpo=None, clave=None):
    req = urllib.request.Request(
        BASE + ruta, method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"x-api-key": clave or vieja, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            t = r.read().decode(errors="replace").strip()
            return r.status, (json.loads(t) if t.startswith(("{", "[", '"')) else t[:200])
    except urllib.error.HTTPError as e:
        return e.code, e.read(400).decode(errors="replace")


s, quien = api("/api/user/current")
if s != 200:
    print("FALLO: la clave vieja no vale (HTTP %s)" % s, file=sys.stderr)
    raise SystemExit(1)

s, claves = api("/api/api-key")
claves = claves if isinstance(claves, list) else []
mia = next((k for k in claves if k.get("name") == "claude-ian"), None)
if not mia:
    print("FALLO: no encuentro la clave llamada claude-ian", file=sys.stderr)
    raise SystemExit(1)
print("clave vieja id=%s nombre=%s" % (mia.get("id"), mia.get("name")), file=sys.stderr)

grupo = mia.get("group", {}).get("id") or 2  # 2 es Administrators por defecto
s, nueva = api("/api/api-key", "POST",
               {"name": "claude-ian-rotada-2026-09-02", "group_id": grupo})
if s not in (200, 201) or not isinstance(nueva, dict) or not nueva.get("unmasked_key"):
    print("FALLO al crear la nueva (HTTP %s): %s" % (s, str(nueva)[:200]), file=sys.stderr)
    raise SystemExit(1)
valor = nueva["unmasked_key"]
print("nueva creada id=%s grupo=%s" % (nueva.get("id"), grupo), file=sys.stderr)

# se comprueba que la nueva sirve ANTES de borrar la vieja
s, q2 = api("/api/user/current", clave=valor)
if s != 200:
    print("FALLO: la nueva no autentica (HTTP %s). NO borro la vieja." % s, file=sys.stderr)
    raise SystemExit(1)
print("la nueva autentica correctamente", file=sys.stderr)

s, r = api("/api/api-key/%s" % mia.get("id"), "DELETE", clave=valor)
print("borrado de la vieja: HTTP %s" % s, file=sys.stderr)

s, _ = api("/api/user/current", clave=vieja)
print("la vieja ahora responde HTTP %s (debe ser 401 o 403)" % s, file=sys.stderr)

# lo unico que va a la salida estandar
sys.stdout.write(valor)
