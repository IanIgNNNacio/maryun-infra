#!/usr/bin/env python3
"""Pone filtros de periodo y proveedor en el tablero 24, Facturas RCV.

Las 16 tarjetas son SQL nativo, asi que un filtro de tablero no se conecta
solo: hay que meter la clausula en cada consulta. Se usan clausulas OPCIONALES
de Metabase -[[AND ...]]-, que desaparecen cuando el filtro esta vacio, de modo
que sin filtro puesto el SQL hace exactamente lo de antes.

Con red, porque son 16 tarjetas de un tablero contable que funciona:

  1. Se guarda el dataset_query de las 16 en /srv/secrets/tablero24-antes.json
  2. Se anota el resultado de cada tarjeta ANTES de tocar nada
  3. Se editan
  4. Se vuelve a ejecutar cada una y se compara con el resultado de antes
  5. Si algo cambio o fallo, se RESTAURA sola desde el respaldo

La clave de API entra por la entrada estandar.

    ... | sudo /srv/bin/mb-filtros-rcv.py            informa
    ... | sudo /srv/bin/mb-filtros-rcv.py --hazlo    aplica
"""
import json
import re
import sys
import urllib.error
import urllib.request

BASE = "http://10.8.0.1:3000"
TABLERO = 24
RESPALDO = "/srv/secrets/tablero24-antes.json"
HAZLO = "--hazlo" in sys.argv
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


s, quien = api("/api/user/current")
if s != 200 or not isinstance(quien, dict) or not quien.get("is_superuser"):
    print("  la clave no vale (HTTP %s)" % s)
    raise SystemExit(1)
print("  autenticado   modo: %s" % ("APLICANDO" if HAZLO else "informa"))

s, tab = api("/api/dashboard/%s" % TABLERO)
tarjetas = []
for dc in tab.get("dashcards", []):
    c = dc.get("card")
    if c and c.get("id") and c not in tarjetas:
        tarjetas.append(c)
# sin repetidas
vistas = {}
for c in tarjetas:
    vistas[c["id"]] = c
tarjetas = list(vistas.values())
print("  tarjetas: %d" % len(tarjetas))


def sql_de(dq):
    """El SQL vive en distinto sitio segun el formato del dataset_query."""
    if "stages" in dq:
        return dq["stages"][0].get("native", "")
    return (dq.get("native") or {}).get("query", "")


def con_sql(dq, nuevo, tags):
    d = json.loads(json.dumps(dq))
    if "stages" in d:
        d["stages"][0]["native"] = nuevo
        d["stages"][0]["template-tags"] = tags
    else:
        d.setdefault("native", {})["query"] = nuevo
        d["native"]["template-tags"] = tags
    return d


# Los alias que usa cada consulta para la tabla que tiene `periodo` y
# `rut_contraparte`. Se detecta leyendo el propio SQL en vez de suponerlo.
def posicion_de(sql, tabla, alias):
    """Donde termina el FROM de la consulta exterior.

    Todo lo que se busque para insertar la clausula tiene que buscarse
    DESPUES de aqui: los CTE de arriba tienen sus propios GROUP BY y WHERE,
    y meter la clausula ahi la deja fuera de alcance.
    """
    patron = r"FROM\s+dwh\.%s\s+(?:AS\s+)?%s\b" % (re.escape(tabla), re.escape(alias))
    ultimo = None
    for m in re.finditer(patron, sql, re.I):
        ultimo = m
    return ultimo.end() if ultimo else 0

def alias_de(sql):
    # Se buscan TODOS los FROM, no el primero: en una consulta con CTEs el
    # primero esta dentro del CTE y su alias no existe fuera. Eso rompio la
    # tarjeta 364 en el primer intento.
    todos = re.findall(r"FROM\s+dwh\.(\w+)\s+(?:AS\s+)?(\w+)", sql, re.I)
    # se descartan las palabras reservadas que pueden colarse como alias
    todos = [(tb, al) for tb, al in todos
             if al.lower() not in ("where", "group", "order", "limit", "having",
                                   "left", "right", "inner", "join", "on", "as")]
    if not todos:
        m = re.search(r"FROM\s+dwh\.(\w+)", sql, re.I)
        return (None, m.group(1), m.end() if m else 0) if m else (None, None, 0)

    # el alias bueno es el que la consulta usa de verdad como <alias>.periodo
    for tabla, alias in reversed(todos):
        if re.search(r"\b%s\.periodo\b" % re.escape(alias), sql, re.I):
            return alias, tabla, posicion_de(sql, tabla, alias)

    # si ninguno la usa, el ultimo FROM es el de la consulta exterior
    tabla, alias = todos[-1]
    return alias, tabla, posicion_de(sql, tabla, alias)


COLUMNAS_RUT = {
    "rcv_documento": "rut_contraparte",
    "rcv_reparto_cuenta": "rut_norm",
    "rcv_reparto_centro_costo": "rut_norm",
    "rcv_incidencia": None,
    "rcv_proveedor": "rut_norm",
}

TAGS = {
    "periodo": {"id": "b1e7c0aa-0001-4000-8000-000000000001", "name": "periodo",
                "display-name": "Periodo", "type": "text"},
    "proveedor": {"id": "b1e7c0aa-0002-4000-8000-000000000002", "name": "proveedor",
                  "display-name": "Proveedor (RUT)", "type": "text"},
}

# Estas dos no aceptan la clausula insertada automaticamente y se dejan
# fuera a proposito:
#
#   363 «Cobertura de clasificacion por dimension»: da «syntax error at or
#       near AND». Su consulta exterior no tiene un unico punto donde
#       colgar la clausula.
#   361 «Pivot cuenta contable x mes»: es tabla dinamica, y Metabase
#       envuelve el SQL para pivotar. La clausula rompe ese envoltorio.
#
# Las dos funcionan sin filtro. Para que lo acepten hay que editarlas a
# mano, mirando su SQL entero; a ciegas ya se rompieron dos veces.
NO_TOCAR = {363, 361}

plan = []
for c in tarjetas:
    if c["id"] in NO_TOCAR:
        plan.append((c, None, "excluida a proposito, ver NO_TOCAR"))
        continue
    dq = c.get("dataset_query") or {}
    sql = sql_de(dq)
    if not sql:
        plan.append((c, None, "sin SQL legible"))
        continue
    if "{{periodo}}" in sql:
        plan.append((c, None, "ya tiene el filtro"))
        continue

    alias, tabla, desde = alias_de(sql)
    if not tabla:
        plan.append((c, None, "no reconozco su FROM"))
        continue
    pre = (alias + ".") if alias else ""
    col_rut = COLUMNAS_RUT.get(tabla)

    trozos = ["[[AND %speriodo = {{periodo}}]]" % pre]
    usa = ["periodo"]
    if col_rut:
        trozos.append("[[AND %s%s = {{proveedor}}]]" % (pre, col_rut))
        usa.append("proveedor")

    # Donde encajar la clausula. Si ya hay WHERE, se cuelga de el; si no, se
    # agrega un WHERE 1=1 antes del primer GROUP/ORDER/LIMIT que aparezca.
    # Todo se busca a partir de `desde`, el final del FROM exterior.
    cola = sql[desde:]
    m = re.search(r"\b(GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING)\b", cola, re.I)
    corte = desde + (m.start() if m else len(cola))
    tiene_where = re.search(r"\bWHERE\b", sql[desde:corte], re.I) is not None
    if tiene_where:
        nuevo = sql[:corte].rstrip() + "\n  " + "\n  ".join(trozos) + "\n" + sql[corte:]
    else:
        nuevo = (sql[:corte].rstrip() + "\nWHERE 1=1\n  " + "\n  ".join(trozos)
                 + "\n" + sql[corte:])

    tags = {k: TAGS[k] for k in usa}
    plan.append((c, (nuevo, tags), "%s (%s)" % (tabla, ", ".join(usa))))

for c, cambio, nota in plan:
    print("   %-5s %-46s %s" % (c["id"], c["name"][:46], nota))

if "--mostrar" in sys.argv:
    quiero = int(sys.argv[sys.argv.index("--mostrar") + 1])
    for c, cambio, nota in plan:
        if c["id"] != quiero:
            continue
        if not cambio:
            print("   nada que cambiar: %s" % nota)
        else:
            print("   --- SQL que quedaria en la tarjeta %d ---" % quiero)
            print(cambio[0])
    raise SystemExit(0)

if not HAZLO:
    raise SystemExit(0)

# ---------------------------------------------------------------- respaldo
antes = {}
for c, _, _ in plan:
    antes[str(c["id"])] = c.get("dataset_query")
with open(RESPALDO, "w", encoding="utf-8") as f:
    json.dump(antes, f, ensure_ascii=False, indent=1)
print("\n  respaldo de los dataset_query: %s" % RESPALDO)


def resultado(idc):
    s, r = api("/api/card/%s/query" % idc, "POST", {})
    if not isinstance(r, dict):
        return None
    d = r.get("data") or {}
    if r.get("status") == "failed" or r.get("error"):
        return "FALLO"
    return json.dumps(d.get("rows"), sort_keys=True)[:4000]


print("  midiendo el estado de antes...")
valor_antes = {}
for c, _, _ in plan:
    valor_antes[c["id"]] = resultado(c["id"])
malas_antes = [i for i, v in valor_antes.items() if v in (None, "FALLO")]
if malas_antes:
    print("  AVISO: estas tarjetas ya fallaban antes de tocar nada: %s" % malas_antes)

# ------------------------------------------------------------------ editar
editadas = []
for c, cambio, _ in plan:
    if not cambio:
        continue
    nuevo, tags = cambio
    dq = con_sql(c["dataset_query"], nuevo, tags)
    s, r = api("/api/card/%s" % c["id"], "PUT", {"dataset_query": dq})
    if s not in (200, 202):
        print("   FALLO editando la tarjeta %s: HTTP %s %s" % (c["id"], s, str(r)[:150]))
        continue
    editadas.append(c["id"])
print("  tarjetas editadas: %d" % len(editadas))

# --------------------------------------------------------------- verificar
print("  comprobando que devuelven lo mismo...")
rotas = []
for idc in editadas:
    ahora = resultado(idc)
    if ahora in (None, "FALLO"):
        if idc not in malas_antes:
            rotas.append((idc, "ahora falla"))
    elif valor_antes.get(idc) not in (None, "FALLO") and ahora != valor_antes[idc]:
        rotas.append((idc, "cambio el resultado"))

if rotas:
    print("\n  PROBLEMA en %d tarjetas: %s" % (len(rotas), rotas))
    print("  restaurando desde el respaldo...")
    for idc, _ in rotas:
        s, r = api("/api/card/%s" % idc, "PUT", {"dataset_query": antes[str(idc)]})
        print("     tarjeta %s restaurada: HTTP %s" % (idc, s))
    print("  NO se agregan los filtros al tablero: primero hay que entender esas tarjetas.")
    raise SystemExit(1)

print("  las %d tarjetas devuelven exactamente lo mismo que antes" % len(editadas))

# --------------------------------------- filtros del tablero y su conexion
parametros = [
    {"id": "b1e7c0aa-0001-4000-8000-000000000001", "name": "Periodo",
     "slug": "periodo", "type": "category", "sectionId": "string"},
    {"id": "b1e7c0aa-0002-4000-8000-000000000002", "name": "Proveedor (RUT)",
     "slug": "proveedor", "type": "category", "sectionId": "string"},
]

s, tab2 = api("/api/dashboard/%s" % TABLERO)
dashcards = []
for dc in tab2.get("dashcards", []):
    nuevo_dc = {k: dc[k] for k in ("id", "card_id", "row", "col", "size_x", "size_y")
                if k in dc}
    nuevo_dc["visualization_settings"] = dc.get("visualization_settings") or {}
    mapeos = []
    c = dc.get("card") or {}
    sql = sql_de(c.get("dataset_query") or {})
    for p in parametros:
        if ("{{%s}}" % p["slug"]) in sql:
            mapeos.append({"parameter_id": p["id"], "card_id": dc.get("card_id"),
                           "target": ["variable", ["template-tag", p["slug"]]]})
    nuevo_dc["parameter_mappings"] = mapeos
    dashcards.append(nuevo_dc)

s, r = api("/api/dashboard/%s" % TABLERO, "PUT",
           {"parameters": parametros, "dashcards": dashcards})
print("  filtros agregados al tablero: HTTP %s" % s)
if s not in (200, 202):
    print("     respuesta: %s" % str(r)[:400])

s, comp = api("/api/dashboard/%s" % TABLERO)
n = sum(len(dc.get("parameter_mappings") or []) for dc in comp.get("dashcards", []))
print("  comprobacion: %d filtros, %d conexiones a tarjetas"
      % (len(comp.get("parameters") or []), n))
