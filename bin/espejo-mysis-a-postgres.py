#!/usr/bin/env python3
"""Replica a Postgres las tablas de MySis que viven en ClickHouse.

Para que: hay funciones de Metabase y Superset que solo andan sobre Postgres
-subir CSV, Actions, validacion de SQL en vivo, cancelar una consulta de
verdad-. Los tableros de produccion SIGUEN en ClickHouse; esto es un espejo.

Como copia, y por que asi:

  ClickHouse  --FORMAT TabSeparated-->  tuberia  --COPY FROM STDIN-->  Postgres

  Los dos formatos coinciden: separador tabulador, barra-N para nulo y el mismo
  escapado de barra invertida. No hay conversion intermedia ni archivo temporal.

  La alternativa era INSERT INTO FUNCTION postgresql(...) desde ClickHouse, que
  es mas corta pero deja la contrasena de Postgres escrita en system.query_log.
  Por eso no se usa.

  Las tablas ReplacingMergeTree se leen con FINAL. Sin eso el espejo se lleva
  las versiones viejas de cada fila y los totales no cuadran con los tableros.

Uso:
    sudo /srv/bin/espejo-mysis-a-postgres.py              lista y no copia
    sudo /srv/bin/espejo-mysis-a-postgres.py --hazlo      copia todo
    sudo /srv/bin/espejo-mysis-a-postgres.py --hazlo --tabla mysis_tab_sku
"""
import re
import shutil
import subprocess
import sys
import time

CH = "clickhouse"
PG = "dwh-postgres"
BASE_CH = "dwh"
ESQUEMA = "mysis"
HAZLO = "--hazlo" in sys.argv
SOLO = sys.argv[sys.argv.index("--tabla") + 1] if "--tabla" in sys.argv else None

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


def pg(consulta, capturar=True):
    r = subprocess.run(
        ["docker", "exec", "-i", "-e", "PGPASSWORD=" + ENV["PG_DWH_PASS"], PG,
         "psql", "-U", ENV["PG_DWH_USER"], "-d", ENV["PG_DWH_DB"],
         "-X", "-tA", "-v", "ON_ERROR_STOP=1", "-c", consulta],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError("Postgres: " + r.stderr.strip()[:300])
    return r.stdout.strip() if capturar else ""


def a_postgres(tipo):
    """Traduce un tipo de ClickHouse al equivalente en Postgres.

    Los envoltorios se pelan en bucle porque se anidan de verdad en estas
    tablas: Nullable(LowCardinality(String)) aparece 32 veces.
    """
    nulo = False
    cambio = True
    while cambio:
        cambio = False
        for envoltorio in ("Nullable", "LowCardinality"):
            m = re.match(r"^%s\((.*)\)$" % envoltorio, tipo)
            if m:
                if envoltorio == "Nullable":
                    nulo = True
                tipo = m.group(1)
                cambio = True

    m = re.match(r"^Decimal\((\d+),\s*(\d+)\)$", tipo)
    if m:
        return "numeric(%s,%s)" % (m.group(1), m.group(2)), nulo
    if re.match(r"^Decimal(32|64|128|256)\(\d+\)$", tipo):
        return "numeric", nulo
    m = re.match(r"^DateTime64\((\d+).*\)$", tipo)
    if m:
        # Postgres llega a 6 decimales; ClickHouse permite hasta 9.
        return "timestamp(%d)" % min(int(m.group(1)), 6), nulo
    if re.match(r"^FixedString\(\d+\)$", tipo):
        return "text", nulo
    if tipo.startswith("Enum"):
        return "text", nulo
    m = re.match(r"^Array\((.*)\)$", tipo)
    if m:
        interno, _ = a_postgres(m.group(1))
        return interno + "[]", nulo
    if tipo.startswith("DateTime"):
        return "timestamp", nulo

    simple = {
        "Int8": "smallint", "Int16": "smallint", "Int32": "integer", "Int64": "bigint",
        "Int128": "numeric", "Int256": "numeric",
        "UInt8": "smallint", "UInt16": "integer", "UInt32": "bigint",
        # UInt64 no cabe en bigint: su maximo es el doble del de bigint.
        "UInt64": "numeric(20,0)", "UInt128": "numeric", "UInt256": "numeric",
        "Float32": "real", "Float64": "double precision",
        "String": "text", "UUID": "uuid", "Date": "date", "Date32": "date",
        "Bool": "boolean", "IPv4": "inet", "IPv6": "inet", "JSON": "jsonb",
    }
    if tipo in simple:
        return simple[tipo], nulo
    # Si aparece un tipo nuevo, texto antes que fallar: esto alimenta tableros,
    # no es la fuente de verdad.
    return "text", nulo


def miles(n):
    return "{:,}".format(int(n)).replace(",", ".")


CATALOGO = """
    SELECT name, engine, total_rows
    FROM system.tables
    WHERE database = '{base}'
      AND (name LIKE 'mysis_%' OR name LIKE 'ventas_mysis%'
           -- `periodos` no lleva el prefijo pero es de MySis y hace falta: la
           -- vista vw_ventas_mysis_periodos, que es la que alimentan los
           -- tableros de ventas, hace LEFT JOIN contra ella para pegar las
           -- fechas de inicio y fin de cada periodo. Sin ella la vista del
           -- espejo saldria con esas dos columnas en blanco.
           OR name = 'periodos')
      AND name NOT LIKE '%_test'
    ORDER BY name""".format(base=BASE_CH)

tablas = []
for linea in ch(CATALOGO).strip().splitlines():
    nombre, motor, nfilas = linea.split("\t")
    if SOLO and nombre != SOLO:
        continue
    tablas.append({"nombre": nombre, "motor": motor, "filas": int(nfilas or 0)})

print("  tablas a espejar: %d   filas en origen: %s"
      % (len(tablas), miles(sum(t["filas"] for t in tablas))))
print("  modo: %s" % ("COPIANDO" if HAZLO else "solo lista"))
print("")

resumen = []
for t in tablas:
    consulta_cols = (
        "SELECT name, type FROM system.columns "
        "WHERE database = '{base}' AND table = '{tabla}' ORDER BY position"
    ).format(base=BASE_CH, tabla=t["nombre"])
    cols = [l.split("\t") for l in ch(consulta_cols).strip().splitlines()]

    definicion = []
    nombres = []
    for nombre, tipo in cols:
        pgtipo, _ = a_postgres(tipo)
        definicion.append('"%s" %s' % (nombre, pgtipo))
        nombres.append('"%s"' % nombre)

    # FINAL solo se permite en los motores que colapsan filas por clave.
    final = " FINAL" if ("Replacing" in t["motor"] or "Collapsing" in t["motor"]) else ""

    if not HAZLO:
        print("  %-42s %12s filas  %2d columnas%s"
              % (t["nombre"], miles(t["filas"]), len(cols),
                 "  con FINAL" if final else ""))
        continue

    inicio = time.time()
    try:
        # Si la tabla ya existe con las MISMAS columnas, se vacia en vez de
        # recrearla. Esto no es una optimizacion: un DROP falla en cuanto hay
        # una vista encima -"cannot drop table because other objects depend on
        # it"- y el refresco programado se caeria todas las noches.
        actual = pg(
            "SELECT string_agg(column_name || ' ' || "
            "  CASE WHEN data_type = 'character varying' THEN 'text' ELSE data_type END, "
            "  ', ' ORDER BY ordinal_position) "
            "FROM information_schema.columns "
            "WHERE table_schema = '%s' AND table_name = '%s'" % (ESQUEMA, t["nombre"]))
        existe = bool(actual)

        if existe:
            # Solo se compara la lista de nombres: los tipos que informa
            # information_schema no se escriben igual que los que se piden al
            # crear (integer/int4, timestamp without time zone/timestamp), y
            # compararlos daria falsos negativos constantes.
            nombres_actuales = [x.strip().split(" ")[0] for x in actual.split(",")]
            nombres_nuevos = [c[0] for c in cols]
            mismo_esquema = nombres_actuales == nombres_nuevos
        else:
            mismo_esquema = False

        if not mismo_esquema:
            # CASCADE se lleva las vistas por delante; vistas.sql las devuelve
            # al final del script.
            pg('DROP TABLE IF EXISTS %s."%s" CASCADE' % (ESQUEMA, t["nombre"]), False)
            pg('CREATE TABLE %s."%s" (%s)' % (ESQUEMA, t["nombre"], ", ".join(definicion)), False)

        lista_ch = ", ".join("`%s`" % c[0] for c in cols)
        select_ch = "SELECT %s FROM %s.`%s`%s FORMAT TabSeparated" % (
            lista_ch, BASE_CH, t["nombre"], final)
        copy_pg = 'COPY %s."%s" (%s) FROM STDIN' % (
            ESQUEMA, t["nombre"], ", ".join(nombres))

        lector = subprocess.Popen(
            ["docker", "exec", "-i", CH, "clickhouse-client", "--query", select_ch],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        # BEGIN, TRUNCATE, COPY y COMMIT viajan por la misma entrada estandar
        # que los datos, en una sola transaccion: mientras corre, los tableros
        # siguen viendo la version anterior completa, y si algo falla no queda
        # una tabla a medias.
        escritor = subprocess.Popen(
            ["docker", "exec", "-i", "-e", "PGPASSWORD=" + ENV["PG_DWH_PASS"], PG,
             "psql", "-U", ENV["PG_DWH_USER"], "-d", ENV["PG_DWH_DB"], "-X",
             "-q", "-v", "ON_ERROR_STOP=1", "-f", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        cabecera = ("BEGIN;" + chr(10)
                    + 'TRUNCATE %s."%s";' % (ESQUEMA, t["nombre"]) + chr(10)
                    # El punto y coma es obligatorio: en un script, sin el, psql
                    # sigue buscando el resto de la sentencia y se come la primera
                    # fila de datos como si fuera SQL. Con -c era opcional.
                    + copy_pg + ";" + chr(10))
        escritor.stdin.write(cabecera.encode())
        escritor.stdin.flush()
        # Se traslada en trozos, sin cargar los 350 MB de la tabla grande en
        # memoria.
        shutil.copyfileobj(lector.stdout, escritor.stdin, 1024 * 256)
        # El punto solo en una linea cierra el COPY; despues se confirma la
        # transaccion. Se escribe con chr() para no pelear con el escapado.
        escritor.stdin.write((chr(92) + '.' + chr(10) + 'COMMIT;' + chr(10)).encode())
        escritor.stdin.close()
        lector.stdout.close()
        _, error_e = escritor.communicate()
        error_l = lector.stderr.read().decode(errors="replace")
        lector.wait()

        if lector.returncode != 0:
            raise RuntimeError("lectura: " + error_l.strip()[:250])
        if escritor.returncode != 0:
            raise RuntimeError("escritura: " + error_e.decode(errors="replace").strip()[:250])

        destino = int(pg('SELECT count(*) FROM %s."%s"' % (ESQUEMA, t["nombre"])))
        origen = int(ch("SELECT count() FROM %s.`%s`%s"
                        % (BASE_CH, t["nombre"], final)).strip())
        pg('ANALYZE %s."%s"' % (ESQUEMA, t["nombre"]), False)

        ok = destino == origen
        resumen.append((t["nombre"], origen, destino, ok, time.time() - inicio, ""))
        print("  %-42s %12s -> %12s  %-9s %5.1fs"
              % (t["nombre"], miles(origen), miles(destino),
                 "ok" if ok else "NO CUADRA", time.time() - inicio))
    except Exception as e:
        resumen.append((t["nombre"], t["filas"], 0, False, time.time() - inicio, str(e)[:200]))
        print("  %-42s FALLO: %s" % (t["nombre"], str(e)[:140]))

if HAZLO:
    bien = [r for r in resumen if r[3]]
    mal = [r for r in resumen if not r[3]]
    print("")
    print("  %d tablas cuadran, %d con problema" % (len(bien), len(mal)))
    print("  filas copiadas: %s" % miles(sum(r[2] for r in bien)))
    for r in mal:
        print("    %s: %s" % (r[0], r[5] or "conteos distintos: %s vs %s" % (r[1], r[2])))
    # Las vistas se reaplican SIEMPRE, no solo cuando algo se recreo: es
    # idempotente y cuesta menos de un segundo, y asi no depende de que el
    # script haya adivinado bien si hacia falta.
    r = subprocess.run(
        ["docker", "exec", "-i", "-e", "PGPASSWORD=" + ENV["PG_DWH_PASS"], PG,
         "psql", "-U", ENV["PG_DWH_USER"], "-d", ENV["PG_DWH_DB"], "-X", "-q",
         "-v", "ON_ERROR_STOP=1", "-f", "-"],
        stdin=open("/srv/stacks/dwh-postgres/vistas.sql", "rb"),
        capture_output=True)
    if r.returncode == 0:
        print("  vistas reaplicadas desde vistas.sql")
    else:
        print("  AVISO: fallaron las vistas: %s"
              % r.stderr.decode(errors="replace").strip()[:250])

    total = pg(
        "SELECT coalesce(pg_size_pretty(sum(pg_total_relation_size("
        "quote_ident(schemaname) || '.' || quote_ident(tablename)))::bigint), '0') "
        "FROM pg_tables WHERE schemaname = '%s'" % ESQUEMA)
    print("  espacio en Postgres: %s" % total)
