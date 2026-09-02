#!/usr/bin/env python3
"""Registra en Metabase el Postgres espejo y activa la subida de archivos.

La clave de API entra por la entrada estandar; la contrasena de Postgres se lee
de /srv/secrets/dwh-postgres.env.

Se conecta como `bi_lector`, que solo lee el esquema `mysis` y solo puede crear
tablas en `manual`. Por eso la subida de CSV se apunta a `manual`: es lo unico
donde ese usuario puede escribir, asi que el espejo no se puede pisar desde la
interfaz ni por error.

Va contra http://10.8.0.1:3000, no contra el nombre publico: por ahi pasa
Cloudflare y responde 403 a los clientes que no parecen navegador.

    ... | sudo /srv/bin/mb-registrar-postgres.py            informa
    ... | sudo /srv/bin/mb-registrar-postgres.py --hazlo    aplica
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://10.8.0.1:3000"
NOMBRE = "Postgres espejo MySis"
HAZLO = "--hazlo" in sys.argv

clave = sys.stdin.read().strip()

ENV = {}
with open("/srv/secrets/dwh-postgres.env", encoding="utf-8") as f:
    for linea in f:
        if "=" in linea and not linea.strip().startswith("#"):
            k, v = linea.strip().split("=", 1)
            ENV[k] = v


def api(ruta, metodo="GET", cuerpo=None):
    req = urllib.request.Request(
        BASE + ruta, method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"x-api-key": clave, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            t = r.read().decode(errors="replace").strip()
            return r.status, (json.loads(t) if t.startswith(("{", "[", '"')) else t[:300])
    except urllib.error.HTTPError as e:
        return e.code, e.read(500).decode(errors="replace")


s, quien = api("/api/user/current")
if s != 200 or not isinstance(quien, dict) or not quien.get("is_superuser"):
    print("  la clave no vale o no es de administrador (HTTP %s)" % s)
    raise SystemExit(1)
print("  autenticado   modo: %s" % ("APLICANDO" if HAZLO else "informa"))

s, bases = api("/api/database")
lista = bases.get("data", bases) if isinstance(bases, dict) else bases
lista = lista if isinstance(lista, list) else []
print("\n== bases ya conectadas")
for b in lista:
    print("   db=%s  %s  (%s)" % (b.get("id"), b.get("name"), b.get("engine")))

ya = next((b for b in lista if b.get("name") == NOMBRE), None)

detalles = {
    "host": "dwh-postgres",
    "port": 5432,
    "dbname": ENV["PG_DWH_DB"],
    "user": ENV["PG_DWH_BI_USER"],
    "password": ENV["PG_DWH_BI_PASS"],
    "ssl": False,
    "tunnel-enabled": False,
    "advanced-options": True,
    # Sin esto Metabase recorre tambien pg_catalog y information_schema.
    "schema-filters-type": "inclusion",
    "schema-filters-patterns": "mysis,manual",
    "let-user-control-scheduling": False,
}

if ya:
    print("\n   ya estaba registrada como db=%s; no la duplico" % ya.get("id"))
    idbase = ya.get("id")
elif not HAZLO:
    print("\n   [simulacion] crear la conexion %r" % NOMBRE)
    raise SystemExit(0)
else:
    s, nueva = api("/api/database", "POST", {
        "name": NOMBRE,
        "engine": "postgres",
        "details": detalles,
        "is_full_sync": True,
        "is_on_demand": False,
    })
    if s not in (200, 201) or not isinstance(nueva, dict) or not nueva.get("id"):
        print("\n   FALLO al crear (HTTP %s): %s" % (s, str(nueva)[:300]))
        raise SystemExit(1)
    idbase = nueva["id"]
    print("\n   creada como db=%s" % idbase)

if not HAZLO:
    raise SystemExit(0)

# Sincronizar el esquema y esperar a que aparezcan las tablas. Sin esto la
# conexion queda creada pero vacia y no se puede armar ningun grafico.
s, _ = api("/api/database/%s/sync_schema" % idbase, "POST")
print("   sincronizacion pedida: HTTP %s" % s)

for intento in range(40):
    time.sleep(6)
    s, tablas = api("/api/database/%s/metadata" % idbase)
    n = len(tablas.get("tables", [])) if isinstance(tablas, dict) else 0
    if n >= 35:
        print("   tablas visibles: %d (tras ~%ds)" % (n, (intento + 1) * 6))
        break
    if intento in (4, 14, 29):
        print("   ... %d tablas hasta ahora" % n)
else:
    print("   AVISO: al cabo de 4 minutos solo hay %d tablas" % n)

# La subida de CSV necesita que se le diga base y esquema. Se apunta a `manual`
# porque es el unico donde bi_lector puede crear.
s, r = api("/api/setting/uploads-settings", "PUT", {
    "value": {"db_id": idbase, "schema_name": "manual", "table_prefix": None}})
print("   subida de archivos apuntada a manual: HTTP %s" % s)
if s not in (200, 204):
    print("      respuesta: %s" % str(r)[:250])

s, v = api("/api/setting/uploads-settings")
print("   comprobacion: %s" % json.dumps(v))
