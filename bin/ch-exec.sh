#!/bin/bash
# Ejecuta una consulta en ClickHouse leyendo la credencial de /srv/secrets.
# La contrasena nunca aparece en la linea de comandos ni en el historial.
#
#   ch-exec.sh "SELECT 1"                  -> ejecuta la consulta
#   ch-exec.sh --insert <base> <tabla>     -> lee datos de stdin en formato Native
set -euo pipefail
. /srv/secrets/clickhouse.env

if [ "${1:-}" = "--insert" ]; then
    exec docker exec -i clickhouse clickhouse-client \
        --user admin --password "$CLICKHOUSE_ADMIN_PASSWORD" \
        --database "$2" --query "INSERT INTO \`$3\` FORMAT Native" \
        --max_insert_block_size 500000
fi

exec docker exec -i clickhouse clickhouse-client \
    --user admin --password "$CLICKHOUSE_ADMIN_PASSWORD" "$@"
