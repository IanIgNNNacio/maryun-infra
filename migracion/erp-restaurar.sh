#!/usr/bin/env bash
# Reintento: pg_restore no admite -j leyendo de la entrada estandar.
# Se copia el volcado DENTRO del contenedor y se restaura desde archivo.
set -euo pipefail
ORIGEN=/tmp/erp-migracion
USUARIO=maryun

paso() { printf '\n=== %s ===\n' "$*"; }

paso "1. copiando los volcados dentro del contenedor"
docker exec maryun-erp-db mkdir -p /tmp/mig
for base in maryun_erp maryun_erp_preview; do
    docker cp "$ORIGEN/$base.dump" maryun-erp-db:/tmp/mig/"$base.dump" >/dev/null
    docker exec maryun-erp-db sh -c "ls -la /tmp/mig/$base.dump" | awk '{printf "  %s %s\n", $5, $9}'
done

paso "2. restaurando desde archivo, en paralelo"
for base in maryun_erp maryun_erp_preview; do
    if docker exec maryun-erp-db pg_restore -U "$USUARIO" -d "$base" \
         --no-owner --no-acl -j 4 "/tmp/mig/$base.dump" 2> "/tmp/restore-$base.log"; then
        echo "  $base: restaurada sin errores"
    else
        echo "  $base: terminada con avisos:"
        sort -u "/tmp/restore-$base.log" | tail -6 | sed 's/^/    /'
    fi
done

paso "3. actualizando estadisticas"
for base in maryun_erp maryun_erp_preview; do
    docker exec maryun-erp-db psql -U "$USUARIO" -d "$base" -q -c "analyze;" 2>/dev/null
done
echo "  hecho"

paso "4. VERIFICACION"
for base in maryun_erp maryun_erp_preview; do
    filas="$(docker exec maryun-erp-db psql -U "$USUARIO" -d "$base" -t -A -c "select coalesce(sum(n_live_tup),0) from pg_stat_user_tables;")"
    tablas="$(docker exec maryun-erp-db psql -U "$USUARIO" -d "$base" -t -A -c "select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema');")"
    tam="$(docker exec maryun-erp-db psql -U "$USUARIO" -d postgres -t -A -c "select pg_size_pretty(pg_database_size('$base'));")"
    printf '  %-22s %s tablas, %s filas, %s\n' "$base" "$tablas" "$filas" "$tam"
done
echo "  (el VPS viejo tenia: maryun_erp 226 tablas / 449.955 filas / 208 MB)"

paso "5. las tablas grandes"
docker exec maryun-erp-db psql -U "$USUARIO" -d maryun_erp -c \
  "select relname, n_live_tup from pg_stat_user_tables order by n_live_tup desc limit 8;" 2>/dev/null | sed 's/^/  /'

paso "6. el esquema local"
docker exec maryun-erp-db psql -U "$USUARIO" -d maryun_erp -t -A -F'|' -c \
  "select table_schema||'.'||table_name from information_schema.tables where table_schema = 'local';" 2>/dev/null | sed 's/^/  /'

paso "7. migraciones de Prisma"
docker exec maryun-erp-db psql -U "$USUARIO" -d maryun_erp -t -A -F'|' -c \
  "select count(*)||' migraciones, ultima: '||max(migration_name) from _prisma_migrations;" 2>/dev/null | sed 's/^/  /'

paso "8. limpieza dentro del contenedor"
docker exec maryun-erp-db rm -rf /tmp/mig
echo "  hecho"

paso "9. levantando las aplicaciones del ERP"
for c in $(docker ps -a --format '{{.Names}}' | grep -E '^[a-z0-9]{20,}-[0-9]+$'); do
    docker start "$c" >/dev/null 2>&1 && echo "  levantado: $c"
done
sleep 30
docker ps --format '  {{.Names}}|{{.Status}}' | grep -E '^  [a-z0-9]{20,}-' | column -t -s'|'
