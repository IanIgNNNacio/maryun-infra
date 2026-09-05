#!/bin/bash
# Ejecuta una consulta en ClickHouse leyendo la credencial de /srv/secrets.
# La contrasena viaja por el entorno del contenedor (docker exec -e NOMBRE, sin
# el valor), no por argv: no aparece en la linea de comandos, ni en `ps -ef`,
# ni en el historial.
#
#   ch-exec.sh "SELECT 1"                  -> ejecuta la consulta
#   ch-exec.sh --insert <base> <tabla>     -> lee datos de stdin en formato Native
set -euo pipefail
. /srv/secrets/clickhouse.env

# clickhouse-client lee CLICKHOUSE_PASSWORD del entorno. Se exporta para que
# `docker exec -e CLICKHOUSE_PASSWORD` lo herede sin escribir el valor en argv.
export CLICKHOUSE_PASSWORD="$CLICKHOUSE_ADMIN_PASSWORD"

if [ "${1:-}" = "--insert" ]; then
    exec docker exec -i -e CLICKHOUSE_PASSWORD clickhouse clickhouse-client \
        --user admin \
        --database "$2" --query "INSERT INTO \`$3\` FORMAT Native" \
        --max_insert_block_size 500000
fi

exec docker exec -i -e CLICKHOUSE_PASSWORD clickhouse clickhouse-client \
    --user admin "$@"
