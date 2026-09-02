#!/usr/bin/env python3
"""Comprueba que los filtros del tablero 24 filtran de verdad.

Que un filtro este puesto no significa que funcione: si el mapeo esta mal, la
tarjeta ignora el valor y devuelve el total. Aqui se compara el resultado sin
filtro contra el resultado con filtro y se exige que CAMBIE.

Tambien se ejecutan las 16 tarjetas para confirmar que ninguna quedo rota.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://10.8.0.1:3000"
TABLERO = 24
clave = sys.stdin.read().strip()


def api(ruta, metodo="GET", cuerpo=None):
    req = urllib.request.Request(
        BASE + ruta, method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"x-api-key": clave, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            t = r.read().decode(errors="replace").strip()
            return r.status, (json.loads(t) if t.startswith(("{", "[", '"')) else t[:300])
    except urllib.error.HTTPError as e:
        return e.code, e.read(600).decode(errors="replace")


s, tab = api("/api/dashboard/%s" % TABLERO)
params = tab.get("parameters") or []
print("  filtros del tablero: %s" % [p.get("slug") for p in params])

idp = {p["slug"]: p["id"] for p in params}


def consulta(dc, valores=None):
    cuerpo = {"parameters": [
        {"id": idp[k], "type": "category", "value": v,
         "target": ["variable", ["template-tag", k]]}
        for k, v in (valores or {}).items()]}
    ruta = "/api/dashboard/%s/dashcard/%s/card/%s/query" % (
        TABLERO, dc["id"], dc["card_id"])
    s, r = api(ruta, "POST", cuerpo)
    if not isinstance(r, dict) or r.get("status") == "failed" or r.get("error"):
        return None, (str(r.get("error"))[:120] if isinstance(r, dict) else str(r)[:120])
    return (r.get("data") or {}).get("rows"), ""


# un periodo que existe de verdad en los datos
PERIODO = "2025-11"

print("")
print("  %-46s %-12s %-12s %s" % ("tarjeta", "sin filtro", "con filtro", "filtra?"))
print("  " + "-" * 86)

rotas = []
filtran = 0
comparables = 0
for dc in tab.get("dashcards", []):
    c = dc.get("card") or {}
    if not c.get("id"):
        continue
    sin, e1 = consulta(dc)
    con, e2 = consulta(dc, {"periodo": PERIODO})
    if sin is None or con is None:
        rotas.append((c["id"], c.get("name"), e1 or e2))
        print("  %-46s ROTA: %s" % (c.get("name", "?")[:46], (e1 or e2)[:40]))
        continue
    n1, n2 = len(sin), len(con)
    # para las tarjetas de un solo numero se compara el valor, no el numero de filas
    if n1 == 1 and n2 == 1:
        cambio = json.dumps(sin) != json.dumps(con)
        etiqueta1, etiqueta2 = str(sin[0][0])[:12], str(con[0][0])[:12]
    else:
        cambio = n1 != n2
        etiqueta1, etiqueta2 = "%d filas" % n1, "%d filas" % n2
    comparables += 1
    if cambio:
        filtran += 1
    print("  %-46s %-12s %-12s %s"
          % (c.get("name", "?")[:46], etiqueta1, etiqueta2, "si" if cambio else "no cambia"))

print("")
print("  tarjetas rotas: %d" % len(rotas))
print("  tarjetas donde el filtro cambia el resultado: %d de %d" % (filtran, comparables))
if filtran < comparables:
    print("  (las que no cambian pueden ser legitimas: una tarjeta que ya mostraba")
    print("   solo un periodo, o una que agrupa por periodo y devuelve una fila por")
    print("   cada uno. Hay que mirarlas una a una.)")
