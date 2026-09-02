#!/usr/bin/env python3
"""Deja a los no administradores en solo lectura, en Metabase.

Que arregla, y por que importa desde hoy:

  1. El grupo «All Users» tenia create-queries = query-builder-and-native sobre
     la base de ClickHouse. Eso es SQL libre: se salta los permisos de
     coleccion y lee cualquier tabla. Con la herramienta detras de la VPN era
     discutible; con metabase.maryun.cl resolviendo a la IP publica, no.

  2. El mismo grupo tenia permiso de MODIFICAR las 21 colecciones, la papelera
     y la raiz incluidas. Cualquiera podia editar o borrar un tablero.

Lo que NO cambia:
  - El grupo Administrators, donde estan Ian y Felipe, se queda igual.
  - view-data sigue «unrestricted»: los tableros se siguen viendo enteros.
  - create-queries queda en «query-builder», no en «no». Asi el interfaz
    grafico y el detalle al pinchar en un grafico siguen funcionando, que es
    como se usa un tablero a diario. Lo que desaparece es escribir SQL.

La clave de API entra por la entrada estandar.
    ... | sudo /srv/bin/mb-permisos.py            solo informa
    ... | sudo /srv/bin/mb-permisos.py --hazlo    aplica
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://10.8.0.1:3000"
clave = sys.stdin.read().strip()
HAZLO = "--hazlo" in sys.argv

# Los grupos que se tocan. Administrators (2) queda fuera a proposito.
GRUPOS = {"1": "All Users", "3": "All tenant users", "4": "Datos Maryun", "5": "Data Analysts"}


def api(ruta, metodo="GET", cuerpo=None):
    req = urllib.request.Request(
        BASE + ruta, method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"x-api-key": clave, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode(errors="replace").strip()
            return r.status, (json.loads(t) if t.startswith(("{", "[", '"')) else t[:250])
    except urllib.error.HTTPError as e:
        return e.code, e.read(500).decode(errors="replace")


s, quien = api("/api/user/current")
if s != 200 or not isinstance(quien, dict) or not quien.get("is_superuser"):
    print("  la clave no vale o no es de administrador (HTTP %s)" % s)
    raise SystemExit(1)
print("  autenticado   modo: %s" % ("APLICANDO" if HAZLO else "solo informa"))

# ---------------------------------------------------------------- datos
print("\n== permisos de datos")
s, g = api("/api/permissions/graph")
if s != 200 or not isinstance(g, dict):
    print("   no pude leer el grafo (HTTP %s): %s" % (s, str(g)[:200]))
    raise SystemExit(1)

cambios_datos = []
for gid, bases in (g.get("groups") or {}).items():
    if gid not in GRUPOS:
        continue
    for dbid, perms in (bases or {}).items():
        if not isinstance(perms, dict):
            continue
        if perms.get("create-queries") == "query-builder-and-native":
            cambios_datos.append((gid, dbid))
            print("   %s / db=%s : query-builder-and-native -> query-builder"
                  % (GRUPOS[gid], dbid))
            if HAZLO:
                perms["create-queries"] = "query-builder"
if not cambios_datos:
    print("   nada que cambiar: ningun grupo no administrador tiene SQL libre")

if HAZLO and cambios_datos:
    s, r = api("/api/permissions/graph", "PUT", g)
    print("   guardado: HTTP %s" % s)
    if s != 200:
        print("   respuesta: %s" % str(r)[:300])

# ----------------------------------------------------------- colecciones
print("\n== permisos de coleccion")
s, cg = api("/api/collection/graph")
if s != 200 or not isinstance(cg, dict):
    print("   no pude leer el grafo de colecciones (HTTP %s)" % s)
    raise SystemExit(1)

# nombres, solo para que el informe se entienda
s, cols = api("/api/collection")
nombre = {str(c.get("id")): c.get("name") for c in (cols if isinstance(cols, list) else [])}

cambios_col = 0
for gid, colecciones in (cg.get("groups") or {}).items():
    if gid not in GRUPOS:
        continue
    for cid, nivel in list((colecciones or {}).items()):
        if nivel == "write":
            cambios_col += 1
            print("   %s / %s (%s) : write -> read"
                  % (GRUPOS[gid], cid, nombre.get(str(cid), "raiz o espacio especial")))
            if HAZLO:
                colecciones[cid] = "read"
if not cambios_col:
    print("   nada que cambiar: ningun grupo no administrador puede modificar")

if HAZLO and cambios_col:
    s, r = api("/api/collection/graph", "PUT", cg)
    print("   guardado: HTTP %s" % s)
    if s != 200:
        print("   respuesta: %s" % str(r)[:300])

if HAZLO:
    print("\n== comprobacion")
    s, g2 = api("/api/permissions/graph")
    libres = [(GRUPOS.get(gid, gid), dbid)
              for gid, bs in (g2.get("groups") or {}).items() if gid in GRUPOS
              for dbid, p in (bs or {}).items()
              if isinstance(p, dict) and p.get("create-queries") == "query-builder-and-native"]
    print("   grupos no administradores con SQL libre: %s" % (libres or "ninguno"))
    s, cg2 = api("/api/collection/graph")
    escritura = [(GRUPOS.get(gid, gid), cid)
                 for gid, cs in (cg2.get("groups") or {}).items() if gid in GRUPOS
                 for cid, n in (cs or {}).items() if n == "write"]
    print("   colecciones que aun pueden modificar: %s" % (escritura or "ninguna"))
    s, ga = api("/api/permissions/graph")
    admin = (ga.get("groups") or {}).get("2", {})
    print("   Administrators sigue con %d bases configuradas" % len(admin))
