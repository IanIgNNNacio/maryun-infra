#!/usr/bin/env python3
"""Crea en Metabase un tablero que compara ClickHouse y el Postgres espejo.

Seis metricas, cada una calculada en los DOS motores sobre los mismos datos y
la misma ventana de tiempo. Sirve para dos cosas a la vez: comprobar que el
espejo devuelve exactamente lo mismo, y ver la diferencia de velocidad.

Las consultas salen de las tarjetas reales del tablero 21 «Dashboard
Principal», traducidas de dialecto:

    ClickHouse                 Postgres
    sumIf(x, cond)             sum(x) FILTER (WHERE cond)
    uniqExact(x)               count(DISTINCT x)
    toStartOfMonth(d)          date_trunc('month', d)::date
    addYears(d, -1)            d - interval '1 year'

La ventana son los ultimos 12 meses contados desde el maximo `facturado` que
haya en los datos, no desde hoy: asi la comparacion no depende del dia en que
se mire y las dos mitades cubren lo mismo.

Se excluyen las mismas sucursales que excluyen las tarjetas originales
-los centros de distribucion y los consumos internos-, porque si no los
numeros no se pueden comparar con el tablero de produccion.

    ... | sudo /srv/bin/mb-tablero-comparacion.py --hazlo
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://10.8.0.1:3000"
DB_CH = 2
DB_PG = 5
HAZLO = "--hazlo" in sys.argv
clave = sys.stdin.read().strip()

EXCLUIR = "('CD SUR', 'CD SANTIAGO', 'CONSUMOS INTERNOS')"

# Cada metrica: nombre, como se ve, y la consulta en cada dialecto.
METRICAS = [
    {
        "nombre": "Ventas netas, ultimos 12 meses",
        "display": "scalar",
        "ch": """
WITH (SELECT max(facturado) FROM dwh.vw_ventas_mysis_periodos) AS fin,
     addMonths(fin, -12) AS ini
SELECT round(sum(totaliza_vta), 0) AS ventas_netas
FROM dwh.vw_ventas_mysis_periodos
WHERE sucursal NOT IN {ex}
  AND facturado > ini AND facturado <= fin""",
        "pg": """
WITH lim AS (
  SELECT max(facturado) AS fin,
         (max(facturado) - interval '12 months')::date AS ini
  FROM mysis.vw_ventas_mysis_periodos
)
SELECT round(sum(totaliza_vta), 0) AS ventas_netas
FROM mysis.vw_ventas_mysis_periodos, lim
WHERE sucursal NOT IN {ex}
  AND facturado > lim.ini AND facturado <= lim.fin""",
    },
    {
        "nombre": "Unidades vendidas, ultimos 12 meses",
        "display": "scalar",
        "ch": """
WITH (SELECT max(facturado) FROM dwh.vw_ventas_mysis_periodos) AS fin,
     addMonths(fin, -12) AS ini
SELECT round(sum(qty), 0) AS unidades
FROM dwh.vw_ventas_mysis_periodos
WHERE sucursal NOT IN {ex}
  AND facturado > ini AND facturado <= fin""",
        "pg": """
WITH lim AS (
  SELECT max(facturado) AS fin,
         (max(facturado) - interval '12 months')::date AS ini
  FROM mysis.vw_ventas_mysis_periodos
)
SELECT round(sum(qty), 0) AS unidades
FROM mysis.vw_ventas_mysis_periodos, lim
WHERE sucursal NOT IN {ex}
  AND facturado > lim.ini AND facturado <= lim.fin""",
    },
    {
        "nombre": "Clientes distintos, ultimos 12 meses",
        "display": "scalar",
        "ch": """
WITH (SELECT max(facturado) FROM dwh.vw_ventas_mysis_periodos) AS fin,
     addMonths(fin, -12) AS ini
SELECT uniqExact(rut) AS clientes
FROM dwh.vw_ventas_mysis_periodos
WHERE sucursal NOT IN {ex}
  AND facturado > ini AND facturado <= fin""",
        "pg": """
WITH lim AS (
  SELECT max(facturado) AS fin,
         (max(facturado) - interval '12 months')::date AS ini
  FROM mysis.vw_ventas_mysis_periodos
)
SELECT count(DISTINCT rut) AS clientes
FROM mysis.vw_ventas_mysis_periodos, lim
WHERE sucursal NOT IN {ex}
  AND facturado > lim.ini AND facturado <= lim.fin""",
    },
    {
        "nombre": "Margen bruto, ultimos 12 meses",
        "display": "scalar",
        "ch": """
WITH (SELECT max(facturado) FROM dwh.vw_ventas_mysis_periodos) AS fin,
     addMonths(fin, -12) AS ini
SELECT round(sum(margen), 0) AS margen_bruto
FROM dwh.vw_ventas_mysis_periodos
WHERE sucursal NOT IN {ex}
  AND facturado > ini AND facturado <= fin""",
        "pg": """
WITH lim AS (
  SELECT max(facturado) AS fin,
         (max(facturado) - interval '12 months')::date AS ini
  FROM mysis.vw_ventas_mysis_periodos
)
SELECT round(sum(margen), 0) AS margen_bruto
FROM mysis.vw_ventas_mysis_periodos, lim
WHERE sucursal NOT IN {ex}
  AND facturado > lim.ini AND facturado <= lim.fin""",
    },
    {
        "nombre": "Ingresos por sucursal, ultimos 12 meses",
        "display": "bar",
        "ch": """
WITH (SELECT max(facturado) FROM dwh.vw_ventas_mysis_periodos) AS fin,
     addMonths(fin, -12) AS ini
SELECT sucursal, round(sum(totaliza_vta), 0) AS ventas
FROM dwh.vw_ventas_mysis_periodos
WHERE sucursal NOT IN {ex}
  AND facturado > ini AND facturado <= fin
GROUP BY sucursal
ORDER BY ventas DESC
LIMIT 12""",
        "pg": """
WITH lim AS (
  SELECT max(facturado) AS fin,
         (max(facturado) - interval '12 months')::date AS ini
  FROM mysis.vw_ventas_mysis_periodos
)
SELECT sucursal, round(sum(totaliza_vta), 0) AS ventas
FROM mysis.vw_ventas_mysis_periodos, lim
WHERE sucursal NOT IN {ex}
  AND facturado > lim.ini AND facturado <= lim.fin
GROUP BY sucursal
ORDER BY ventas DESC
LIMIT 12""",
    },
    {
        "nombre": "Tendencia mensual, ultimos 24 meses",
        "display": "line",
        "ch": """
WITH (SELECT max(facturado) FROM dwh.vw_ventas_mysis_periodos) AS fin,
     addMonths(fin, -24) AS ini
SELECT toStartOfMonth(facturado) AS mes, round(sum(totaliza_vta), 0) AS ventas
FROM dwh.vw_ventas_mysis_periodos
WHERE sucursal NOT IN {ex}
  AND facturado > ini AND facturado <= fin
GROUP BY mes
ORDER BY mes""",
        "pg": """
WITH lim AS (
  SELECT max(facturado) AS fin,
         (max(facturado) - interval '24 months')::date AS ini
  FROM mysis.vw_ventas_mysis_periodos
)
SELECT date_trunc('month', facturado)::date AS mes,
       round(sum(totaliza_vta), 0) AS ventas
FROM mysis.vw_ventas_mysis_periodos, lim
WHERE sucursal NOT IN {ex}
  AND facturado > lim.ini AND facturado <= lim.fin
GROUP BY mes
ORDER BY mes""",
    },
]


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


s, quien = api("/api/user/current")
if s != 200 or not isinstance(quien, dict) or not quien.get("is_superuser"):
    print("  la clave no vale (HTTP %s)" % s)
    raise SystemExit(1)
print("  autenticado   modo: %s" % ("APLICANDO" if HAZLO else "informa"))

if not HAZLO:
    for m in METRICAS:
        print("   %-42s %s" % (m["nombre"], m["display"]))
    raise SystemExit(0)

# Coleccion propia, para no ensuciar las que ya existen.
NOMBRE_COL = "Comparacion Postgres vs ClickHouse"
s, cols = api("/api/collection")
existente = next((c for c in (cols if isinstance(cols, list) else [])
                  if c.get("name") == NOMBRE_COL), None)
if existente:
    idcol = existente["id"]
    print("   la coleccion ya existia: %s" % idcol)
else:
    s, c = api("/api/collection", "POST", {
        "name": NOMBRE_COL,
        "description": "Las mismas metricas calculadas en ClickHouse y en el Postgres "
                       "espejo. Si una fila no coincide, el espejo esta mal.",
    })
    if s not in (200, 201):
        print("   FALLO creando la coleccion (HTTP %s): %s" % (s, str(c)[:200]))
        raise SystemExit(1)
    idcol = c["id"]
    print("   coleccion creada: %s" % idcol)

creadas = []
for m in METRICAS:
    for motor, idbase, etiqueta in (("ch", DB_CH, "ClickHouse"), ("pg", DB_PG, "Postgres")):
        sql = m[motor].format(ex=EXCLUIR).strip()
        s, card = api("/api/card", "POST", {
            "name": "%s · %s" % (m["nombre"], etiqueta),
            "display": m["display"],
            "dataset_query": {"database": idbase, "type": "native",
                              "native": {"query": sql}},
            "visualization_settings": {},
            "collection_id": idcol,
            "description": "Motor: %s. La pareja de esta tarjeta calcula lo mismo en el otro motor."
                           % etiqueta,
        })
        if s not in (200, 201) or not isinstance(card, dict) or not card.get("id"):
            print("   FALLO %-42s %-10s HTTP %s: %s"
                  % (m["nombre"], etiqueta, s, str(card)[:220]))
            continue
        creadas.append({"id": card["id"], "metrica": m["nombre"], "motor": motor,
                        "display": m["display"]})
        print("   tarjeta %-5s %-42s %s" % (card["id"], m["nombre"][:42], etiqueta))

if not creadas:
    print("\n   no se creo ninguna tarjeta; no armo el tablero")
    raise SystemExit(1)

# El tablero: una fila por metrica, ClickHouse a la izquierda y Postgres a la
# derecha, para que la comparacion se lea de un golpe.
s, tabs = api("/api/dashboard")
prev = next((d for d in (tabs.get("data", tabs) if isinstance(tabs, dict) else tabs)
             if isinstance(d, dict) and d.get("name") == "Postgres espejo vs ClickHouse"), None)
if prev:
    idtab = prev["id"]
    print("\n   el tablero ya existia: %s" % idtab)
else:
    s, d = api("/api/dashboard", "POST", {
        "name": "Postgres espejo vs ClickHouse",
        "collection_id": idcol,
        "description": "Cada fila es la misma metrica en los dos motores: ClickHouse a la "
                       "izquierda, el Postgres espejo a la derecha. Los numeros deben "
                       "coincidir; lo que cambia es el tiempo de respuesta.",
    })
    if s not in (200, 201):
        print("\n   FALLO creando el tablero (HTTP %s): %s" % (s, str(d)[:250]))
        raise SystemExit(1)
    idtab = d["id"]
    print("\n   tablero creado: %s" % idtab)

dashcards = []
fila = 0
temporal = -1
for m in METRICAS:
    alto = 4 if m["display"] == "scalar" else 7
    for col, motor in ((0, "ch"), (9, "pg")):
        c = next((x for x in creadas if x["metrica"] == m["nombre"] and x["motor"] == motor), None)
        if not c:
            continue
        dashcards.append({
            "id": temporal, "card_id": c["id"],
            "row": fila, "col": col, "size_x": 9, "size_y": alto,
            "parameter_mappings": [], "visualization_settings": {},
        })
        temporal -= 1
    fila += alto

s, r = api("/api/dashboard/%s" % idtab, "PUT", {"dashcards": dashcards})
print("   %d tarjetas colocadas: HTTP %s" % (len(dashcards), s))
if s not in (200, 202):
    print("      respuesta: %s" % str(r)[:400])

s, comp = api("/api/dashboard/%s" % idtab)
n = len(comp.get("dashcards", [])) if isinstance(comp, dict) else 0
print("   comprobacion: el tablero tiene %d tarjetas" % n)
print("   URL: https://metabase.maryun.cl/dashboard/%s" % idtab)
