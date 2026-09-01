#!/usr/bin/env bash
#
# Parte 1 de 2: vuelca las bases del ERP en el VPS VIEJO.
#
# Antes de volcar detiene las aplicaciones que escriben. Un volcado de Postgres
# es consistente por si mismo gracias a MVCC, pero detenerlas ademas evita que
# el VPS siga divergiendo despues de la copia: el DNS ya apunta a maryun01, asi
# que a partir de aqui el viejo no debe recibir mas escrituras.
#
# Incluye el esquema 'local' (tabla estado_replica, el estado de la replicacion
# MySis -> ERP), que faltaba por completo en maryun01.

set -euo pipefail

SALIDA=/tmp/erp-migracion
USUARIO=maryun

paso() { printf '\n=== %s ===\n' "$*"; }

mkdir -p "$SALIDA"

paso "1. deteniendo lo que escribe en el ERP"
PARADOS=""
for c in $(docker ps --format '{{.Names}}' | grep -iE 'erp-web|^[a-z0-9]{20,}-[0-9]+$'); do
    docker stop "$c" >/dev/null 2>&1 && PARADOS="$PARADOS $c"
done
echo "  detenidos:${PARADOS:- ninguno}"
sleep 3

paso "2. conexiones que quedan"
docker exec maryun-erp-db psql -U "$USUARIO" -d postgres -t -A -F'|' \
  -c "select datname, count(*) from pg_stat_activity where datname like 'maryun_erp%' group by 1;" \
  2>/dev/null | sed 's/^/  /' || echo "  ninguna"

paso "3. estado de partida (para comparar despues)"
for base in maryun_erp maryun_erp_preview; do
    filas="$(docker exec maryun-erp-db psql -U "$USUARIO" -d "$base" -t -A \
        -c "select coalesce(sum(n_live_tup),0) from pg_stat_user_tables;" 2>/dev/null)"
    tablas="$(docker exec maryun-erp-db psql -U "$USUARIO" -d "$base" -t -A \
        -c "select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema');" 2>/dev/null)"
    printf '  %-22s %s tablas, %s filas\n' "$base" "$tablas" "$filas"
done

paso "4. esquemas presentes (debe aparecer 'local')"
docker exec maryun-erp-db psql -U "$USUARIO" -d maryun_erp -t -A \
  -c "select nspname from pg_namespace where nspname not like 'pg_%' and nspname <> 'information_schema' order by 1;" \
  2>/dev/null | sed 's/^/  /'

paso "5. volcando"
for base in maryun_erp maryun_erp_preview; do
    # -Fc = formato comprimido y restaurable en paralelo.
    # --no-owner / --no-acl: el destino tiene su propio dueno y sus propios permisos.
    docker exec maryun-erp-db pg_dump -U "$USUARIO" -d "$base" \
        -Fc --no-owner --no-acl > "$SALIDA/$base.dump"
    printf '  %-22s %s\n' "$base.dump" "$(du -h "$SALIDA/$base.dump" | cut -f1)"
done

paso "6. sumas de verificacion"
cd "$SALIDA" && sha256sum ./*.dump | sed 's/^/  /'

paso "7. contenido del volcado (comprobacion de que no esta vacio)"
for base in maryun_erp maryun_erp_preview; do
    n="$(docker exec -i maryun-erp-db pg_restore -l < "$SALIDA/$base.dump" 2>/dev/null | grep -c 'TABLE DATA' || true)"
    printf '  %-22s %s tablas con datos\n' "$base" "$n"
done

echo
echo "=== listo. Aplicaciones detenidas:${PARADOS:- ninguna} ==="
echo "=== si hay que revertir:  docker start${PARADOS} ==="
