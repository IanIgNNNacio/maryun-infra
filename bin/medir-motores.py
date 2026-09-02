#!/usr/bin/env python3
"""Mide la misma consulta en ClickHouse y en el Postgres espejo.

Se mide contra los motores directamente, sin pasar por Metabase, para que el
numero no lleve dentro la latencia de la aplicacion.

Cuatro ejecuciones por consulta y motor: la primera se descarta -Postgres carga
paginas a memoria y ClickHouse llena su cache de marcas- y de las otras tres se
toma la mediana.

IMPORTANTE, y es la razon de que haya dos bloques:

  Medir sobre la VISTA no compara motores, compara definiciones de vista. La
  vista de ClickHouse esta escrita como LEFT JOIN mas GROUP BY v.* con any(),
  que es la forma de ClickHouse de quedarse con una fila de la derecha; eso
  obliga a agrupar por las 57 columnas de 1,65 millones de filas. La vista
  equivalente en Postgres es un LEFT JOIN normal, porque la clave del otro lado
  es unica. Comparar las dos mide sobre todo ese GROUP BY.

  Por eso se mide tambien contra la TABLA BASE, donde las dos consultas son
  identicas. Ese es el numero que habla de los motores.

  Los dos bloques importan: el de la vista es el que sienten los tableros de
  produccion, porque es lo que consultan de verdad.
"""
import statistics
import subprocess
import time

ENV = {}
with open("/srv/secrets/dwh-postgres.env", encoding="utf-8") as f:
    for linea in f:
        if "=" in linea and not linea.strip().startswith("#"):
            k, v = linea.strip().split("=", 1)
            ENV[k] = v

CORTE_CH = "facturado >= '2024-01-01' AND facturado <= '2024-12-31'"
CORTE_PG = "facturado >= DATE '2024-01-01' AND facturado <= DATE '2024-12-31'"


def juego(origen_ch, origen_pg):
    return [
        {
            "nombre": "Suma sobre todas las filas",
            "ch": "SELECT sum(totaliza_vta) FROM " + origen_ch,
            "pg": "SELECT sum(totaliza_vta) FROM " + origen_pg,
        },
        {
            "nombre": "Suma con filtro de un ano",
            "ch": "SELECT sum(totaliza_vta) FROM " + origen_ch + " WHERE " + CORTE_CH,
            "pg": "SELECT sum(totaliza_vta) FROM " + origen_pg + " WHERE " + CORTE_PG,
        },
        {
            "nombre": "Agrupado por sucursal",
            "ch": "SELECT sucursal, sum(totaliza_vta) FROM " + origen_ch
                  + " WHERE " + CORTE_CH + " GROUP BY sucursal ORDER BY 2 DESC",
            "pg": "SELECT sucursal, sum(totaliza_vta) FROM " + origen_pg
                  + " WHERE " + CORTE_PG + " GROUP BY sucursal ORDER BY 2 DESC",
        },
        {
            "nombre": "Serie mensual completa",
            "ch": "SELECT toStartOfMonth(facturado) m, sum(totaliza_vta) FROM "
                  + origen_ch + " GROUP BY m ORDER BY m",
            "pg": "SELECT date_trunc('month', facturado)::date m, sum(totaliza_vta) FROM "
                  + origen_pg + " GROUP BY m ORDER BY m",
        },
        {
            "nombre": "Clientes distintos (cardinalidad alta)",
            "ch": "SELECT uniqExact(rut) FROM " + origen_ch + " WHERE " + CORTE_CH,
            "pg": "SELECT count(DISTINCT rut) FROM " + origen_pg + " WHERE " + CORTE_PG,
        },
        {
            "nombre": "Cruce con la maestra de SKU",
            "ch": "SELECT s.familia_id, sum(v.totaliza_vta) FROM " + origen_ch + " v "
                  "LEFT JOIN dwh.mysis_tab_sku s ON s.sku = v.sku WHERE " + CORTE_CH
                  + " GROUP BY s.familia_id ORDER BY 2 DESC LIMIT 20",
            "pg": "SELECT s.familia_id, sum(v.totaliza_vta) FROM " + origen_pg + " v "
                  "LEFT JOIN mysis.mysis_tab_sku s ON s.sku = v.sku WHERE " + CORTE_PG
                  + " GROUP BY s.familia_id ORDER BY 2 DESC LIMIT 20",
        },
        {
            "nombre": "Top 50 por importe",
            "ch": "SELECT factura, rso, totaliza_vta FROM " + origen_ch
                  + " WHERE " + CORTE_CH + " ORDER BY totaliza_vta DESC LIMIT 50",
            "pg": "SELECT factura, rso, totaliza_vta FROM " + origen_pg
                  + " WHERE " + CORTE_PG + " ORDER BY totaliza_vta DESC LIMIT 50",
        },
    ]


def correr_ch(sql):
    r = subprocess.run(
        ["docker", "exec", "-i", "clickhouse", "clickhouse-client", "--query", sql],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.returncode == 0, r.stderr.strip()[:150]


def correr_pg(sql):
    r = subprocess.run(
        ["docker", "exec", "-i", "-e", "PGPASSWORD=" + ENV["PG_DWH_PASS"], "dwh-postgres",
         "psql", "-U", ENV["PG_DWH_USER"], "-d", ENV["PG_DWH_DB"], "-X", "-tA",
         "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.returncode == 0, r.stderr.strip()[:150]


def medir(fn, sql):
    tiempos = []
    for i in range(4):
        t0 = time.time()
        ok, err = fn(sql)
        t1 = time.time()
        if not ok:
            return None, err
        if i > 0:
            tiempos.append(t1 - t0)
    return statistics.median(tiempos), ""


for titulo, och, opg in [
    ("TABLA BASE — las dos consultas son identicas, esto compara motores",
     "dwh.ventas_mysis", "mysis.ventas_mysis"),
    ("VISTA — lo que consultan los tableros de verdad",
     "dwh.vw_ventas_mysis_periodos", "mysis.vw_ventas_mysis_periodos"),
]:
    print("")
    print("  == " + titulo)
    print("  %-40s %10s %10s %9s" % ("consulta", "ClickHouse", "Postgres", "razon"))
    print("  " + "-" * 74)
    for c in juego(och, opg):
        tch, ech = medir(correr_ch, c["ch"])
        tpg, epg = medir(correr_pg, c["pg"])
        if tch is None or tpg is None:
            print("  %-40s FALLO" % c["nombre"][:40])
            if ech:
                print("      ClickHouse: %s" % ech)
            if epg:
                print("      Postgres:   %s" % epg)
            continue
        razon = tpg / tch if tch > 0 else 0
        print("  %-40s %9.3fs %9.3fs %8.2fx" % (c["nombre"][:40], tch, tpg, razon))

print("")
print("  La razon es cuantas veces mas tarda Postgres: menos de 1 significa que")
print("  Postgres gano. Los tiempos incluyen el arranque del cliente en los dos")
print("  casos -unos 20 ms-, asi que en las consultas muy rapidas la razon queda")
print("  comprimida hacia 1 y no hay que leerla como un empate real.")
