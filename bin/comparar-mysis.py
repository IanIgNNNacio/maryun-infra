#!/usr/bin/env python3
"""Compara MySis contra el DWH de ClickHouse, con la MISMA definicion de periodo.

Ojo con esto, que es lo que hace que las cifras «no calcen» cuando se comparan
a mano: `dwh.ventas_mysis.periodo` NO es el mes calendario. Es una columna
MATERIALIZED definida asi:

    formatDateTime(addMonths(facturado, if(toDayOfMonth(facturado) >= 26, 1, 0)), '%Y-%m')

O sea, un periodo comercial que va del 26 al 25: todo lo facturado el 26 o
despues cuenta para el mes siguiente. Comparar eso contra
DATE_FORMAT(dt_out,'%Y-%m') de MySis da diferencias de mil y pico lineas por
mes que no son un error de datos, son dos definiciones distintas.

Aqui se aplica la misma regla a los dos lados.

Ademas se acota el DWH a las lineas que vienen de mstr_pedidos: la tabla
tambien recibe notas de credito (mstr_nc) y anexos (mstr_anexo) por el UNION
del cargador, y contarlas contra las ventas seria comparar de mas.

    sudo /srv/bin/comparar-mysis.py            compara
    sudo /srv/bin/comparar-mysis.py --limpiar  ademas borra el mapeo al terminar
"""
import subprocess
import sys

import yaml

CFG = "/srv/stacks/mage/project/Maryun/io_config.yaml"
BASE = "mysis_ro"
LIMPIAR = "--limpiar" in sys.argv

cfg = yaml.safe_load(open(CFG, encoding="utf-8"))["maryun"]
HOST = "%s:%s" % (cfg["MYSQL_HOST"], cfg["MYSQL_PORT"])
DB = cfg["MYSQL_DATABASE"]
USER = cfg["MYSQL_USER"]
PASS = cfg["MYSQL_PASSWORD"]


def ch(consulta, formato="PrettyCompactMonoBlock"):
    r = subprocess.run(
        ["docker", "exec", "-i", "clickhouse", "clickhouse-client", "--multiquery",
         "--query", consulta + (" FORMAT " + formato if formato else "")],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.replace(PASS, "(oculto)").strip()[:500])
    return r.stdout


ch("DROP DATABASE IF EXISTS %s" % BASE, None)
ch("CREATE DATABASE %s ENGINE = MySQL('%s', '%s', '%s', '%s')"
   % (BASE, HOST, DB, USER, PASS), None)
print("  MySis montado como %s (solo lectura)" % BASE)

PERIODOS = "'2025-09','2025-10','2025-11','2025-12','2026-01'"

# la misma regla del 26 que usa la columna MATERIALIZED del DWH
PERIODO_MYSIS = ("formatDateTime(addMonths(toDate(o.dt_out), "
                 "if(toDayOfMonth(toDate(o.dt_out)) >= 26, 1, 0)), '%Y-%m')")

ORIGEN = """
    FROM {b}.mstr_pedidos o
    INNER JOIN {b}.mstr_pedidos_aux a ON o.pid = a.pid
    INNER JOIN {b}.tab_sku s ON a.sku = s.sku
    INNER JOIN {b}.tab_clientes p ON toInt64OrNull(toString(o.cliente_id)) = toInt64OrNull(toString(p.cliente_id))
    INNER JOIN {b}.tab_users u ON toInt64OrNull(toString(o.usr_in)) = toInt64OrNull(toString(u.user_id))
    INNER JOIN {b}.tab_bodegas bo ON toInt64OrNull(toString(o.sucursal_id)) = toInt64OrNull(toString(bo.bodega_id))
    WHERE o.factura IS NOT NULL
      AND toInt64OrNull(toString(o.direccion_id)) != 0
      AND {per} IN ({p})
""".format(b=BASE, p=PERIODOS, per=PERIODO_MYSIS)

print("")
print("== lineas y documentos, con la MISMA regla de periodo en los dos lados")
print(ch("""
WITH origen AS (
    SELECT {per} AS periodo, count() AS lineas, uniqExact(o.pid) AS documentos,
           round(sum(toFloat64OrZero(toString(a.entrega)) + toFloat64OrZero(toString(a.picking)))) AS unidades,
           round(sum((toFloat64OrZero(toString(a.entrega)) + toFloat64OrZero(toString(a.picking)))
                     * toFloat64OrZero(toString(a.pu)))) AS venta
    {origen}
    GROUP BY periodo
),
espejo AS (
    -- solo las lineas que vienen de mstr_pedidos, no las notas de credito ni los anexos
    SELECT v.periodo AS periodo, count() AS lineas, uniqExact(v.pid) AS documentos,
           round(sum(toFloat64(v.qty))) AS unidades,
           round(sum(toFloat64(v.totaliza_vta))) AS venta
    FROM dwh.ventas_mysis v
    WHERE v.periodo IN ({p})
      AND v.pid IN (SELECT pid FROM {b}.mstr_pedidos)
    GROUP BY v.periodo
)
SELECT o.periodo AS periodo,
       o.lineas AS lin_mysis, e.lineas AS lin_dwh, e.lineas - o.lineas AS dif_lin,
       o.documentos AS doc_mysis, e.documentos AS doc_dwh,
       o.unidades AS uni_mysis, e.unidades AS uni_dwh,
       o.venta AS vta_mysis, e.venta AS vta_dwh,
       round(100.0 * (e.venta - o.venta) / o.venta, 3) AS dif_venta_pct
FROM origen o
LEFT JOIN espejo e ON e.periodo = o.periodo
ORDER BY periodo
""".format(origen=ORIGEN, p=PERIODOS, b=BASE, per=PERIODO_MYSIS)))

if LIMPIAR:
    ch("DROP DATABASE IF EXISTS %s" % BASE, None)
    print("  mapeo %s borrado" % BASE)
else:
    print("  el mapeo %s queda montado; borralo con --limpiar" % BASE)
