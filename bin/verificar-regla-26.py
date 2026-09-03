#!/usr/bin/env python3
"""Comprueba que la regla del 26 ya no queda en ningun sitio y que todo corre.

No basta con que el texto ya no diga 26: hay que ejecutar cada consulta, porque
un reemplazo puede dejar SQL sintacticamente roto y eso solo se ve al correrlo.
"""
import json
import re
import subprocess


def psql(cont, usr, base, q):
    r = subprocess.run(
        ["docker", "exec", "-i", cont, "psql", "-U", usr, "-d", base, "-X", "-tA", "-c", q],
        capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else "ERROR: " + r.stderr[:200]


def ch(sql):
    r = subprocess.run(
        ["docker", "exec", "-i", "clickhouse", "clickhouse-client", "--query", sql],
        capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip()[:160]


print("== queda la regla en algun sitio?")
for etiqueta, cont, usr, base, q in [
    ("Superset", "superset-db", "superset", "superset",
     "SELECT count(*) FROM tables WHERE sql ~* 'toDayOfMonth[^)]*\\)\\s*>=\\s*26' "
     "OR sql ~* 'DAY\\(CURDATE\\(\\)\\)\\s*>=\\s*26' OR sql ~* 'DAY\\(facturado\\)\\s*>=\\s*26'"),
    ("Metabase", "metabase-db", "maryun", "maryun_db",
     "SELECT count(*) FROM report_card WHERE archived = false AND ("
     "dataset_query ~* 'toDayOfMonth[^)]*\\)\\s*>=\\s*26' OR "
     "dataset_query ~* 'DAY\\([^)]*\\)\\s*>=\\s*26')"),
]:
    print("   %-10s %s objetos con la regla (debe ser 0)" % (etiqueta, psql(cont, usr, base, q).strip()))

ok, err = ch("SELECT count() FROM system.columns WHERE database='dwh' "
             "AND default_expression LIKE '%26%' AND default_expression LIKE '%onth%'")
print("   ClickHouse columnas con la regla: (debe ser 0)")
r = subprocess.run(["docker", "exec", "-i", "clickhouse", "clickhouse-client", "--query",
                    "SELECT count() FROM system.columns WHERE database='dwh' "
                    "AND default_expression LIKE '%26%' AND default_expression LIKE '%onth%'"],
                   capture_output=True, text=True)
print("      %s" % r.stdout.strip())
r = subprocess.run(["docker", "exec", "-i", "clickhouse", "clickhouse-client", "--query",
                    "SELECT count() FROM system.tables WHERE database='dwh' AND engine LIKE '%View%' "
                    "AND create_table_query LIKE '%>= 26%'"],
                   capture_output=True, text=True)
print("   ClickHouse vistas con la regla: %s (debe ser 0)" % r.stdout.strip())

print("")
print("== cada consulta de Superset sigue ejecutando?")
ids = [l for l in psql("superset-db", "superset", "superset",
                       "SELECT id FROM tables WHERE sql IS NOT NULL AND sql <> '' ORDER BY id").splitlines()
       if l.strip()]
malas = 0
for idt in ids:
    sql = psql("superset-db", "superset", "superset", "SELECT sql FROM tables WHERE id = %s" % idt)
    nombre = psql("superset-db", "superset", "superset",
                  "SELECT coalesce(table_name,'?') FROM tables WHERE id = %s" % idt).strip()
    if not sql.strip():
        continue
    # se envuelve para no traer todas las filas
    ok, err = ch("SELECT count() FROM (%s) LIMIT 1" % sql.rstrip().rstrip(";"))
    if ok:
        print("   [%s] %-36s ok" % (idt, nombre[:36]))
    else:
        malas += 1
        print("   [%s] %-36s FALLA: %s" % (idt, nombre[:36], err))

print("")
print("   conjuntos de datos con problema: %d" % malas)

print("")
print("== la tarjeta 41 de Metabase")
crudo = psql("metabase-db", "maryun", "maryun_db",
             "SELECT dataset_query FROM report_card WHERE id = 41")
d = json.loads(crudo)
sql = d["stages"][0]["native"] if "stages" in d else d["native"]["query"]
# las clausulas opcionales de Metabase no son SQL valido fuera de Metabase
limpio = re.sub(r"\[\[.*?\]\]", "", sql).rstrip().rstrip(";")
ok, err = ch("SELECT count() FROM (%s) LIMIT 1" % limpio)
print("   ejecuta: %s" % ("si" if ok else "NO -> " + err))
print("   agrupa por: %s" % ("mes calendario" if "toStartOfMonth" in sql else "REVISAR"))
