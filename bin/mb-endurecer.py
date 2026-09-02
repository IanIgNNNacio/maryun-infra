#!/usr/bin/env python3
"""Endurece Metabase ahora que metabase.maryun.cl esta en internet.

La clave de API se lee por la ENTRADA ESTANDAR, no por argumento, para que no
aparezca en `ps` ni en el historial:

    cat clave.txt | sudo /srv/bin/mb-endurecer.py           solo informa
    cat clave.txt | sudo /srv/bin/mb-endurecer.py --hazlo   aplica

Va contra http://10.8.0.1:3000 y no contra el nombre publico a proposito: por
el nombre publico pasa Cloudflare, que responde 403 «error 1010» a cualquier
cliente que no parezca un navegador. Eso ya frena los scripts, pero tambien
frenaria a este.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://10.8.0.1:3000"
HAZLO = "--hazlo" in sys.argv

clave = sys.stdin.read().strip()
if not clave:
    print("  no llego ninguna clave por la entrada estandar")
    raise SystemExit(1)


def api(ruta, metodo="GET", cuerpo=None):
    req = urllib.request.Request(
        BASE + ruta, method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"x-api-key": clave, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            t = r.read().decode(errors="replace").strip()
            if not t:
                return r.status, None
            # ojo: "" es subcadena de todo, asi que startswith y no `in`
            return r.status, (json.loads(t) if t.startswith(("{", "[", '"')) else t[:200])
    except urllib.error.HTTPError as e:
        return e.code, e.read(300).decode(errors="replace")


s, quien = api("/api/user/current")
if s != 200 or not isinstance(quien, dict) or not quien.get("is_superuser"):
    print("  la clave no vale o no es de administrador (HTTP %s)" % s)
    raise SystemExit(1)
print("  autenticado como %s (admin)" % quien.get("email"))
print("  modo: %s" % ("APLICANDO" if HAZLO else "solo informa"))

print("\n== ajustes antes")
AJUSTES = ["password-complexity", "enable-public-sharing", "enable-embedding-static",
           "enable-password-login", "session-timeout"]
antes = {}
for k in AJUSTES:
    s, v = api("/api/setting/" + k)
    antes[k] = v
    print("   %-26s %s" % (k, json.dumps(v)))

print("\n== tableros y preguntas con enlace publico")
s, tab = api("/api/dashboard/public")
s2, pre = api("/api/card/public")
tab = tab if isinstance(tab, list) else []
pre = pre if isinstance(pre, list) else []
for t in tab:
    print("   tablero %s  \"%s\"" % (t.get("id"), t.get("name")))
for p in pre:
    print("   pregunta %s  \"%s\"" % (p.get("id"), p.get("name")))
if not tab and not pre:
    print("   ninguno")

print("\n== cambios")

# 1 · complejidad de contrasena. Solo afecta a contrasenas nuevas o cambiadas:
#     no invalida las que ya existen, asi que hay que avisar a quien tenga una debil.
if antes.get("password-complexity") in ("strong", {"total": 8}) :
    print("   complejidad: ya estaba en strong")
elif HAZLO:
    s, r = api("/api/setting/password-complexity", "PUT", {"value": "strong"})
    print("   complejidad -> strong: HTTP %s" % s)
else:
    print("   [simulacion] complejidad -> strong")

# 2 · compartir publicamente. Estaba sin fijar, que en Metabase equivale a
#     desactivado, pero dejarlo explicito evita que se active sin querer y
#     revive los enlaces viejos que siguen guardados.
if antes.get("enable-public-sharing") is False:
    print("   compartir publico: ya estaba desactivado de forma explicita")
elif HAZLO:
    s, r = api("/api/setting/enable-public-sharing", "PUT", {"value": False})
    print("   compartir publico -> false explicito: HTTP %s" % s)
else:
    print("   [simulacion] compartir publico -> false explicito")

# 3 · revocar los enlaces publicos que quedaron guardados. Con el ajuste
#     desactivado no funcionan, pero si alguien lo activa vuelven a la vida.
for t in tab:
    if HAZLO:
        s, r = api("/api/dashboard/%s/public_link" % t.get("id"), "DELETE")
        print("   enlace del tablero %s revocado: HTTP %s" % (t.get("id"), s))
    else:
        print("   [simulacion] revocar enlace del tablero %s" % t.get("id"))
for p in pre:
    if HAZLO:
        s, r = api("/api/card/%s/public_link" % p.get("id"), "DELETE")
        print("   enlace de la pregunta %s revocado: HTTP %s" % (p.get("id"), s))
    else:
        print("   [simulacion] revocar enlace de la pregunta %s" % p.get("id"))

if HAZLO:
    print("\n== ajustes despues")
    for k in AJUSTES:
        s, v = api("/api/setting/" + k)
        marca = "  <- cambiado" if json.dumps(v) != json.dumps(antes[k]) else ""
        print("   %-26s %s%s" % (k, json.dumps(v), marca))
