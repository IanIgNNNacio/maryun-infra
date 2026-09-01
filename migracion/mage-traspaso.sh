#!/usr/bin/env bash
#
# Traspasa los triggers de Mage del VPS viejo a maryun01.
#
# ORDEN IMPORTANTE: primero se apagan los del VPS y despues se encienden los de
# maryun01. Si se hiciera al reves, MySis -la base de produccion del ERP-
# recibiria durante un rato la carga de DOS servidores a la vez.
#
# Reparto nuevo de los nocturnos: hoy los 19 disparan todos a las 00:00 UTC
# (20:00 en Chile). Cumplen la regla de no tocar MySis entre las 7 y las 19,
# pero llegan como una estampida. Se escalonan cada 5 minutos entre las 00:00 y
# las 01:35 UTC (20:00 a 21:35 en Chile), holgado para duraciones de 22 a 45 s.
#
# Las cron van en UTC (verificado empiricamente contra las corridas reales).
#
# Uso:  mage-traspaso.sh apagar-viejo | encender-nuevo | ver

set -euo pipefail

ACCION="${1:?uso: apagar-viejo | encender-nuevo | ver}"

# pipeline_uuid -> nueva expresion cron (UTC). Solo los que hoy son @daily.
read -r -d '' HORARIOS <<'TABLA' || true
my_sis_to_clickhouse|0 0 * * *
mysis_tabla_tab_clientes_to_clickhouse|10 0 * * *
mysis_tabla_tab_clientes_precios_to_clickhouse|15 0 * * *
mysis_tabla_tab_proveedores_to_clickhouse|20 0 * * *
mysis_tabla_tab_familias_to_clickhouse|25 0 * * *
mysis_tabla_tab_marcas_to_clickhouse|30 0 * * *
mysis_tabla_tab_tipos_to_clickhouse|35 0 * * *
mysis_tabla_tab_users_to_clickhouse|40 0 * * *
mysis_tabla_tab_bancos_to_clickhouse|45 0 * * *
mysis_tabla_tab_fpagos_to_clickhouse|50 0 * * *
mysis_tabla_mstr_anexo_to_clickhouse|55 0 * * *
mysis_tabla_mstr_caja_to_clickhouse|0 1 * * *
mysis_tabla_mstr_matrices_to_clickhouse|5 1 * * *
mysis_tabla_mstr_oc_to_clickhouse|10 1 * * *
mysis_tabla_mstr_oc_aux_to_clickhouse|15 1 * * *
mysis_tabla_mstr_pagos_to_clickhouse|20 1 * * *
mysis_tabla_mstr_pedidos_creditos_to_clickhouse|25 1 * * *
mysis_tabla_mstr_pedidos_creditos_pid_to_clickhouse|30 1 * * *
mysis_tabla_mstr_pedidos_pagos_to_clickhouse|35 1 * * *
TABLA

case "$ACCION" in

apagar-viejo)
    echo "=== VPS viejo: estado antes ==="
    docker exec mage-mage-db-1 psql -U mage -d mage -t -A -F'|' \
      -c "select status, count(*) from pipeline_schedule group by 1 order by 1;" | sed 's/^/  /'
    echo
    echo "=== apagando TODOS los triggers del VPS viejo ==="
    docker exec mage-mage-db-1 psql -U mage -d mage -q \
      -c "update pipeline_schedule set status = 'INACTIVE' where status::text = 'ACTIVE';"
    echo
    echo "=== estado despues ==="
    docker exec mage-mage-db-1 psql -U mage -d mage -t -A -F'|' \
      -c "select status, count(*) from pipeline_schedule group by 1 order by 1;" | sed 's/^/  /'
    echo
    echo "  A partir de aqui el VPS viejo NO vuelve a consultar MySis."
    ;;

encender-nuevo)
    echo "=== maryun01: aplicando el reparto nuevo a los nocturnos ==="
    while IFS='|' read -r uuid cron; do
        [ -z "$uuid" ] && continue
        n="$(docker exec mage-db psql -U mage -d mage -t -A -c \
             "update pipeline_schedule set schedule_interval = '$cron'
              where pipeline_uuid = '$uuid' and schedule_interval = '@daily'
              returning 1;" | grep -c 1 || true)"
        printf '  %-52s %-12s (%s trigger)\n' "$uuid" "$cron" "${n:-0}"
    done <<< "$HORARIOS"

    echo
    echo "=== encendiendo los triggers que estaban activos en el viejo ==="
    # Los @once ya se ejecutaron en su dia; se dejan apagados a proposito para
    # que el servidor nuevo no los repita.
    docker exec mage-db psql -U mage -d mage -q -c "
      update pipeline_schedule set status = 'ACTIVE'
      where (schedule_type::text = 'API'
             or (schedule_type::text = 'TIME'
                 and schedule_interval is not null
                 and schedule_interval <> '@once'));"

    echo
    echo "=== estado final ==="
    docker exec mage-db psql -U mage -d mage -t -A -F'|' \
      -c "select coalesce(status::text,'null'), count(*) from pipeline_schedule group by 1 order by 1;" | sed 's/^/  /'
    ;;

ver)
    CT="${2:-mage-db}"
    echo "=== triggers activos con horario ==="
    docker exec "$CT" psql -U mage -d mage -t -A -F'|' -c "
      select pipeline_uuid, schedule_type, coalesce(schedule_interval,'-')
      from pipeline_schedule
      where status::text = 'ACTIVE' and schedule_type::text = 'TIME'
      order by
        case when schedule_interval ~ '^[0-9]' then
             lpad(split_part(schedule_interval,' ',2),2,'0') || lpad(split_part(schedule_interval,' ',1),2,'0')
             else 'zz' end,
        pipeline_uuid;" \
      | awk -F'|' '{printf "  %-52s %-6s %s\n", substr($1,1,52), $2, $3}'
    echo
    echo "=== resumen ==="
    docker exec "$CT" psql -U mage -d mage -t -A -F'|' -c "
      select coalesce(status::text,'null') || ' ' || coalesce(schedule_type::text,'-'), count(*)
      from pipeline_schedule group by 1 order by 1;" | sed 's/^/  /'
    ;;
esac
