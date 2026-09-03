#!/usr/bin/env python3
"""Quita la regla del periodo comercial 26-25 de Metabase y Superset.

La regla existia para calcular las comisiones de los vendedores sobre un ciclo
que iba del 26 de un mes al 25 del siguiente. Ya no se usa: los periodos pasan a
ser meses calendario.

En ClickHouse ya se cambio aparte: las columnas MATERIALIZED de ventas_mysis y
ventas_mysis_2, la vista materializada mv_facturas_periodos, el contenido de
dwh.periodos y la vista vw_ventas_mysis_periodos.

Aqui van los dos consumidores:

  Metabase, tarjeta 41 «comparacion mes actual / anterior»
  Superset, 7 conjuntos de datos

Todo se respalda antes en /srv/secrets/regla26-antes-2026-09-03.json.

    sudo /srv/bin/quitar-regla-26.py            informa
    sudo /srv/bin/quitar-regla-26.py --hazlo    aplica
"""
import json
import re
import subprocess
import sys

HAZLO = "--hazlo" in sys.argv
RESPALDO = "/srv/secrets/regla26-antes-2026-09-03.json"

# ------------------------------------------------------------------ util
def psql(contenedor, usuario, base, consulta, valores=None):
    cmd = ["docker", "exec", "-i", contenedor, "psql", "-U", usuario, "-d", base,
           "-X", "-tA", "-v", "ON_ERROR_STOP=1"]
    if valores is None:
        cmd += ["-c", consulta]
        entrada = None
    else:
        cmd += ["-f", "-"]
        entrada = consulta
    r = subprocess.run(cmd, input=entrada, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:400])
    return r.stdout


def mb(consulta, valores=None):
    return psql("metabase-db", "maryun", "maryun_db", consulta, valores)


def sup(consulta, valores=None):
    return psql("superset-db", "superset", "superset", consulta, valores)


# --------------------------------------------------------- transformaciones
# Patron A: la ventana del ciclo actual, en los datasets de Superset.
# Se sustituye por el mes calendario en curso.
CICLO_DESDE = re.compile(
    r"facturado\s*>=\s*CASE\s+WHEN\s+DAY\(CURDATE\(\)\)\s*>=\s*26\s+"
    r"THEN\s+DATE_FORMAT\(CURDATE\(\),\s*'%Y-%m-26'\)\s+"
    r"ELSE\s+DATE_FORMAT\(DATE_SUB\(CURDATE\(\),\s*INTERVAL\s+1\s+MONTH\),\s*'%Y-%m-26'\)\s+END",
    re.I | re.S)
CICLO_HASTA = re.compile(
    r"facturado\s*<=\s*CASE\s+WHEN\s+DAY\(CURDATE\(\)\)\s*>=\s*26\s+"
    r"THEN\s+DATE_FORMAT\(DATE_ADD\(CURDATE\(\),\s*INTERVAL\s+1\s+MONTH\),\s*'%Y-%m-25'\)\s+"
    r"ELSE\s+DATE_FORMAT\(CURDATE\(\),\s*'%Y-%m-25'\)\s+END",
    re.I | re.S)

# Patron B: el desplazamiento del mes, en los datasets de «ciclo».
DESPLAZA = re.compile(
    r"if\(\s*toDayOfMonth\(\s*(\w+)\s*\)\s*>=\s*26\s*,\s*addMonths\(\s*\1\s*,\s*1\s*\)\s*,\s*\1\s*\)",
    re.I | re.S)


def limpiar(sql):
    """Devuelve (sql_nuevo, cuantos_cambios)."""
    n = 0
    nuevo, k = CICLO_DESDE.subn("facturado >= toStartOfMonth(today())", sql)
    n += k
    nuevo, k = CICLO_HASTA.subn("facturado <= toLastDayOfMonth(today())", nuevo)
    n += k
    nuevo, k = DESPLAZA.subn(r"\1", nuevo)
    n += k
    # el comentario que explicaba la regla deja de tener sentido
    nuevo = re.sub(r"^\s*--\s*Determina la fecha de ciclo.*$", "", nuevo, flags=re.M | re.I)
    return nuevo, n


# ------------------------------------------------------------------ Superset
respaldo = {"superset": {}, "metabase": {}}

filas = [l for l in sup(
    "SELECT id FROM tables WHERE sql ILIKE '%26%' "
    "AND (sql ILIKE '%onth%' OR sql ILIKE '%periodo%') ORDER BY id").splitlines() if l.strip()]

print("== Superset")
for idt in filas:
    sql = sup("SELECT sql FROM tables WHERE id = %s" % idt)
    nombre = sup("SELECT coalesce(table_name,'?') FROM tables WHERE id = %s" % idt).strip()
    respaldo["superset"][idt] = sql
    nuevo, n = limpiar(sql)
    if n == 0:
        print("   [%s] %-34s sin cambios" % (idt, nombre[:34]))
        continue
    if "26" in nuevo and re.search(r">=\s*26", nuevo):
        print("   [%s] %-34s AVISO: aun queda un '>= 26', revisar a mano" % (idt, nombre[:34]))
    print("   [%s] %-34s %d sustitucion(es)" % (idt, nombre[:34], n))
    if HAZLO:
        sup("UPDATE tables SET sql = $QQ$%s$QQ$ WHERE id = %s;" % (nuevo, idt), valores=True)

# ------------------------------------------------------------------ Metabase
print("")
print("== Metabase")
crudo = mb("SELECT dataset_query FROM report_card WHERE id = 41")
respaldo["metabase"]["41"] = crudo
d = json.loads(crudo)
etapa = d["stages"][0] if "stages" in d else None
sql_viejo = etapa["native"] if etapa else d["native"]["query"]

SQL_NUEVO = """SELECT
  toStartOfMonth(facturado) AS mes,
  ROUND(SUM(totaliza_vta)) AS total_ventas
FROM dwh.vw_ventas_mysis_periodos
WHERE 1=1
[[AND {{sucursal}} ]]
[[AND {{rut_vendedor}} ]]
GROUP BY mes
ORDER BY mes;"""

if "toDayOfMonth" in sql_viejo or ">= 26" in sql_viejo:
    print("   [41] comparacion mes actual / anterior: se reescribe el SQL")
    print("        antes: agrupaba por mes de ciclo (del 26 al 25)")
    print("        ahora: agrupa por mes calendario")
    if HAZLO:
        if etapa is not None:
            d["stages"][0]["native"] = SQL_NUEVO
        else:
            d["native"]["query"] = SQL_NUEVO
        mb("UPDATE report_card SET dataset_query = $QQ$%s$QQ$ WHERE id = 41;"
           % json.dumps(d, ensure_ascii=False), valores=True)
else:
    print("   [41] ya estaba sin la regla")

if HAZLO:
    with open(RESPALDO, "w", encoding="utf-8") as f:
        json.dump(respaldo, f, ensure_ascii=False, indent=1)
    print("")
    print("   respaldo en %s" % RESPALDO)
