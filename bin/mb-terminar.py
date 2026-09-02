#!/usr/bin/env python3
"""Termina los dos cambios que fallaron en el primer intento.

1. password-complexity: el PUT con "strong" dio HTTP 500. Este script primero
   PREGUNTA a Metabase que valores acepta el ajuste, en vez de adivinar.

2. El enlace publico del tablero 19: el DELETE dio 400 porque ya habia
   desactivado «compartir publicamente», y con el ajuste apagado la ruta se
   niega a operar. Hay que revocar PRIMERO y apagar despues. Aqui se reactiva
   unos segundos, se revoca y se vuelve a apagar; queda comprobado al final.

La clave entra por la entrada estandar.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://10.8.0.1:3000"
clave = sys.stdin.read().strip()
HAZLO = "--hazlo" in sys.argv


def api(ruta, metodo="GET", cuerpo=None):
    req = urllib.request.Request(
        BASE + ruta, method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"x-api-key": clave, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            t = r.read().decode(errors="replace").strip()
            return r.status, (json.loads(t) if t.startswith(("{", "[", '"')) else t[:250])
    except urllib.error.HTTPError as e:
        return e.code, e.read(400).decode(errors="replace")


s, _ = api("/api/user/current")
print("  autenticado: HTTP %s   modo: %s" % (s, "APLICANDO" if HAZLO else "solo informa"))

print("\n== que acepta password-complexity")
s, todos = api("/api/setting")
definicion = next((x for x in (todos if isinstance(todos, list) else [])
                   if x.get("key") == "password-complexity"), None)
if definicion:
    for k in ("value", "default", "type", "options", "enum"):
        if k in definicion:
            print("   %-10s %s" % (k, str(definicion[k])[:160]))
else:
    print("   el ajuste no aparece en /api/setting")

# Metabase guarda esto como un mapa; el keyword "strong" es lo que acepta la
# variable de entorno, no la API. Se prueba con el mapa explicito.
objetivo = {"total": 10, "digit": 1, "upper": 1, "special": 1}
print("\n== complejidad de contrasena")
if HAZLO:
    for intento in ({"value": objetivo}, {"value": json.dumps(objetivo)}, {"value": "strong"}):
        s, r = api("/api/setting/password-complexity", "PUT", intento)
        print("   intento con %-34s -> HTTP %s" % (str(intento["value"])[:34], s))
        if s in (200, 204):
            break
    s, v = api("/api/setting/password-complexity")
    print("   ahora vale: %s" % json.dumps(v))
else:
    print("   [simulacion] fijar %s" % objetivo)

print("\n== enlace publico del tablero 19")
s, tab = api("/api/dashboard/public")
print("   antes: %s" % (json.dumps(tab)[:160] if tab else "la ruta no devuelve nada con el ajuste apagado"))
if HAZLO:
    s1, _ = api("/api/setting/enable-public-sharing", "PUT", {"value": True})
    print("   reactivado temporalmente: HTTP %s" % s1)
    s2, tab2 = api("/api/dashboard/public")
    ids = [t.get("id") for t in (tab2 if isinstance(tab2, list) else [])]
    print("   con enlace ahora mismo: %s" % (ids or "ninguno"))
    for i in ids:
        s3, r3 = api("/api/dashboard/%s/public_link" % i, "DELETE")
        print("   revocado el del tablero %s: HTTP %s" % (i, s3))
    s4, _ = api("/api/setting/enable-public-sharing", "PUT", {"value": False})
    print("   vuelto a apagar: HTTP %s" % s4)
    s5, v5 = api("/api/setting/enable-public-sharing")
    print("   comprobacion final del ajuste: %s" % json.dumps(v5))
else:
    print("   [simulacion] reactivar, revocar y volver a apagar")
