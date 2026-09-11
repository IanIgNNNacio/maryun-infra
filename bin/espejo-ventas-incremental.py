#!/usr/bin/env python3
"""Trae a Postgres las ventas que entraron a ClickHouse desde la ultima vez.

Para que: mysis.ventas_mysis en dwh-postgres es la mitad grande de
global.ventas, la vista que alimenta los tableros «global». El volcado
completo -espejo-mysis-a-postgres.py- corre una vez al dia a las 07:30 UTC, y
eso dejaba los tableros hasta veinticuatro horas por detras. ClickHouse recibe
de MySis cada cinco minutos; esto acerca el espejo a esa frescura.

Por que no correr el volcado completo cada quince minutos, que seria una linea:
porque copia la tabla entera con TRUNCATE + COPY, y el TRUNCATE toma ACCESS
EXCLUSIVE durante los diez segundos que dura. En horario laboral eso es un
tablero congelado cada cuarto de hora. Este guion solo INSERTA.

Como funciona:

  1. marca de agua = max(ingested_at) del espejo
  2. ClickHouse devuelve las filas con ingested_at >= marca
  3. van a una tabla de paso
  4. se insertan las que no estan ya, comparando por (pid, sku)

El >= no es un descuido. ingested_at tiene resolucion de segundo y un lote
puede repartirse entre dos corridas: con > se perderian las filas que
compartan segundo con la ultima traida. El anti-join por (pid, sku) hace que
repetirlas no cueste nada.

LO QUE ESTE GUION NO HACE, y por eso el volcado nocturno sigue existiendo:
  - no borra. Si alguien borra filas en ClickHouse, aqui quedan de fantasma
    hasta el volcado de las 07:30.
  - no actualiza. El exportador de Mage tampoco: una vez cargada una linea,
    sus columnas deuda, pmp, factura y entregado quedan fijas. El saldo vivo
    de cobranza esta en Receivable del ERP, no aqui.
  - no toca las otras 36 tablas del espejo.

Uso:
    sudo /srv/bin/espejo-ventas-incremental.py            dice que traeria
    sudo /srv/bin/espejo-ventas-incremental.py --hazlo    lo trae
"""
import shutil
import subprocess
import sys
import time

CH = "clickhouse"
PG = "dwh-postgres"
BASE_CH = "dwh"
ESQUEMA = "mysis"
TABLA = "ventas_mysis"
PASO = "_ventas_incremental_paso"
HAZLO = "--hazlo" in sys.argv

ENV = {}
with open("/srv/secrets/dwh-postgres.env", encoding="utf-8") as f:
    for linea in f:
        if "=" in linea and not linea.strip().startswith("#"):
            k, v = linea.strip().split("=", 1)
            ENV[k] = v


def ch(consulta, formato="TabSeparated"):
    r = subprocess.run(
        ["docker", "exec", "-i", CH, "clickhouse-client", "--query",
         consulta + " FORMAT " + formato],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError("ClickHouse: " + r.stderr.strip()[:300])
    return r.stdout


def pg(consulta):
    r = subprocess.run(
        ["docker", "exec", "-i", "-e", "PGPASSWORD=" + ENV["PG_DWH_PASS"], PG,
         "psql", "-U", ENV["PG_DWH_USER"], "-d", ENV["PG_DWH_DB"],
         "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", consulta],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError("Postgres: " + r.stderr.strip()[:300])
    return r.stdout.strip()


def main():
    inicio = time.time()

    # Las columnas salen de ClickHouse y no de Postgres a proposito: las
    # calculadas (ALIAS y MATERIALIZED) no aparecen en un SELECT *, hay que
    # nombrarlas. Es el mismo criterio que usa el volcado completo.
    cols = [l.split("\t")[0] for l in ch(
        "SELECT name FROM system.columns WHERE database = '%s' AND table = '%s' "
        "ORDER BY position" % (BASE_CH, TABLA)).strip().splitlines()]

    cols_pg = pg(
        "SELECT string_agg(column_name, ',' ORDER BY ordinal_position) "
        "FROM information_schema.columns "
        "WHERE table_schema = '%s' AND table_name = '%s'" % (ESQUEMA, TABLA)).split(",")

    # Si los dos lados dejaron de coincidir, el COPY posicional meteria cada
    # valor en la columna equivocada sin quejarse. Mejor parar: el volcado
    # completo de la noche recrea la tabla y arregla la discrepancia.
    if cols != cols_pg:
        print("  ABORTA: las columnas no coinciden.")
        print("    ClickHouse: %s" % ",".join(cols))
        print("    Postgres:   %s" % ",".join(cols_pg))
        return 2

    marca = pg('SELECT max(ingested_at) FROM %s."%s"' % (ESQUEMA, TABLA))
    if not marca:
        print("  ABORTA: el espejo esta vacio, no hay marca de agua. "
              "Corre antes el volcado completo.")
        return 2

    nuevas = int(ch("SELECT count() FROM %s.%s WHERE ingested_at >= '%s'"
                    % (BASE_CH, TABLA, marca)).strip() or 0)

    print("  marca de agua: %s" % marca)
    print("  filas en ClickHouse desde esa marca: %d" % nuevas)
    if not HAZLO:
        print("  modo: solo lista. Con --hazlo se insertan las que falten.")
        return 0
    if nuevas == 0:
        print("  nada que traer.")
        return 0

    lista = ", ".join('"%s"' % c for c in cols)

    # La tabla de paso es UNLOGGED: no se replica ni se respalda, y si el
    # motor se cae a mitad se pierde, que es exactamente lo que se quiere.
    pg('CREATE UNLOGGED TABLE IF NOT EXISTS %s."%s" '
       '(LIKE %s."%s" INCLUDING DEFAULTS)' % (ESQUEMA, PASO, ESQUEMA, TABLA))
    pg('TRUNCATE %s."%s"' % (ESQUEMA, PASO))

    origen = subprocess.Popen(
        ["docker", "exec", "-i", CH, "clickhouse-client", "--query",
         "SELECT %s FROM %s.%s WHERE ingested_at >= '%s' FORMAT TabSeparated"
         % (lista, BASE_CH, TABLA, marca)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)

    destino = subprocess.Popen(
        ["docker", "exec", "-i", "-e", "PGPASSWORD=" + ENV["PG_DWH_PASS"], PG,
         "psql", "-U", ENV["PG_DWH_USER"], "-d", ENV["PG_DWH_DB"],
         "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c",
         'COPY %s."%s" (%s) FROM STDIN' % (ESQUEMA, PASO, lista)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    shutil.copyfileobj(origen.stdout, destino.stdin, 1024 * 256)
    destino.stdin.close()
    origen.stdout.close()
    err_ch = origen.stderr.read().decode(errors="replace")
    origen.wait()
    _, err_pg = destino.communicate()

    if origen.returncode != 0 or destino.returncode != 0:
        print("  FALLO la copia. ClickHouse: %s | Postgres: %s"
              % (err_ch.strip()[:200], err_pg.decode(errors="replace").strip()[:200]))
        return 1

    en_paso = int(pg('SELECT count(*) FROM %s."%s"' % (ESQUEMA, PASO)) or 0)

    insertadas = pg(
        'WITH nuevas AS ('
        '  INSERT INTO {e}."{t}" ({c}) '
        '  SELECT {c} FROM {e}."{p}" p '
        '  WHERE NOT EXISTS (SELECT 1 FROM {e}."{t}" v '
        '                    WHERE v.pid = p.pid AND v.sku = p.sku) '
        '  RETURNING 1) '
        'SELECT count(*) FROM nuevas'.format(e=ESQUEMA, t=TABLA, p=PASO, c=lista))

    pg('TRUNCATE %s."%s"' % (ESQUEMA, PASO))

    total = pg('SELECT count(*) FROM %s."%s"' % (ESQUEMA, TABLA))
    hasta = pg('SELECT max(facturado) FROM %s."%s"' % (ESQUEMA, TABLA))
    print("  traidas %s, insertadas %s (el resto ya estaba)"
          % (en_paso, insertadas))
    print("  espejo: %s filas, hasta %s, en %.1f s"
          % (total, hasta, time.time() - inicio))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("  ERROR: %s" % e)
        sys.exit(1)
