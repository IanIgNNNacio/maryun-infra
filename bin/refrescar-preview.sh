#!/usr/bin/env bash
#
# Refresca el entorno de preview del ERP con los datos de produccion.
#
# Para que: en preview se prueba antes de pasar a produccion, y sin datos reales
# las pruebas no reproducen los casos que importan. Esto reemplaza al
# scripts/replica/sync.sh del VPS viejo, que traia produccion desde Neon cada
# hora y de paso podia refrescar preview. Ese dependia de Neon y de Vercel, que
# ya no existen; aqui las dos bases viven en el mismo PostgreSQL, asi que la
# copia es local y tarda segundos.
#
#   maryun_erp        --(pg_dump | pg_restore)-->  maryun_erp_preview
#   R2 maryun-erp     --(rclone sync)----------->  R2 maryun-erp-preview
#
# Se copia TODO, sin anonimizar: decision de Ian el 4-sep-2026. El control de
# acceso de preview es el mismo que el de produccion -el mismo login- y solo dos
# personas entran, asi que copiar en claro no amplia la exposicion.
#
# Por que `sync` y no `copy` en el bucket, al contrario que en el respaldo: el
# respaldo debe conservar lo que se borro en produccion, pero preview tiene que
# ser un ESPEJO. Con `copy` iria acumulando objetos huerfanos de pruebas viejas
# cuyas filas ya no existen en la base recien restaurada.
#
# Al terminar se reinicia el contenedor de preview. No es cosmetica: su
# entrypoint aplica las migraciones pendientes al arrancar, asi que si la rama
# de preview tiene migraciones que produccion todavia no tiene, el reinicio las
# vuelve a aplicar sobre los datos recien copiados.
#
# PARA CONGELAR PREVIEW mientras se prueba algo:
#
#     sudo touch /srv/PREVIEW-CONGELADO      # el refresco se salta
#     sudo rm /srv/PREVIEW-CONGELADO         # vuelve a refrescar
#
# Uso:  refrescar-preview.sh [--forzar]
#         --forzar  refresca aunque exista el centinela

set -uo pipefail

CONTENEDOR_DB=maryun-erp-db
ORIGEN=maryun_erp
DESTINO=maryun_erp_preview
CENTINELA=/srv/PREVIEW-CONGELADO
CONFIG_ORIGEN=/srv/secrets/r2-respaldo.env
CONFIG_PREVIEW=/srv/secrets/preview-r2.env
LOG=/var/log/maryun-preview.log

FORZAR=0
[ "${1:-}" = "--forzar" ] && FORZAR=1

log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }

telegram() {
    . /srv/secrets/monitoreo.env 2>/dev/null || true
    [ -n "${TELEGRAM_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT:-}" ] || return 0
    local texto="${1//%0A/$'\n'}"
    curl -s -m 15 -o /dev/null \
        --data "chat_id=$TELEGRAM_CHAT" --data "parse_mode=HTML" \
        --data-urlencode "text=$texto" \
        "https://api.telegram.org/bot$TELEGRAM_TOKEN/sendMessage" || true
}

morir() { log "ERROR: $*"; telegram "Refresco de preview - $*"; exit 1; }

# ─────────────────────────────────────────────────────────── guardarrailes ──
# Esto BORRA una base entera. El nombre del destino se comprueba literalmente
# antes de tocar nada: un despiste con la variable no puede acabar en
# produccion.
[ "$DESTINO" = "maryun_erp_preview" ] || morir "el destino no es la base de preview: $DESTINO"
[ "$DESTINO" != "$ORIGEN" ] || morir "origen y destino son la misma base"

if [ -e "$CENTINELA" ] && [ "$FORZAR" = 0 ]; then
    log "preview congelado ($CENTINELA existe), no se refresca"
    exit 0
fi

exec 9>/var/lock/maryun-preview.lock
flock -n 9 || { log "ya hay un refresco en marcha, salgo"; exit 0; }

docker inspect "$CONTENEDOR_DB" >/dev/null 2>&1 || morir "no existe el contenedor $CONTENEDOR_DB"

log "=== refresco de preview"

# ────────────────────────────────────────────────────────────── la base ──
antes="$(docker exec "$CONTENEDOR_DB" psql -U maryun -d postgres -tAc \
    "SELECT pg_size_pretty(pg_database_size('$DESTINO'))" 2>/dev/null)"
log "  preview antes: ${antes:-no existia}"

# WITH (FORCE) corta las conexiones abiertas de la aplicacion de preview. Sin
# eso el DROP se queda esperando indefinidamente a que suelten la base.
docker exec "$CONTENEDOR_DB" psql -U maryun -d postgres -q \
    -c "DROP DATABASE IF EXISTS $DESTINO WITH (FORCE)" \
    -c "CREATE DATABASE $DESTINO OWNER maryun" \
    || morir "no se pudo recrear la base de preview"

# Tuberia directa, sin archivo intermedio: lo que sale del volcado entra en la
# restauracion. Son unos 6 segundos para 289 MB.
#
# El estado se captura ANTES de filtrar la salida. Encadenar el docker exec a un
# grep o un tail hace que $? sea el del filtro, que casi siempre acierta, y el
# fallo real pasa desapercibido.
salida_db="$(docker exec "$CONTENEDOR_DB" sh -c \
    "pg_dump -U maryun -Fc -d $ORIGEN | pg_restore -U maryun -d $DESTINO --no-owner --no-privileges" 2>&1)"
codigo_db=$?
[ -n "$salida_db" ] && printf '%s\n' "$salida_db" | tail -5 | sed 's/^/    /'
[ "$codigo_db" -eq 0 ] || morir "fallo la copia de la base a preview"

filas="$(docker exec "$CONTENEDOR_DB" psql -U maryun -d "$DESTINO" -tAc \
    "SELECT count(*) FROM \"SiiDocument\"" 2>/dev/null)"
[ -n "$filas" ] && [ "$filas" -gt 0 ] 2>/dev/null \
    || morir "la base de preview quedo vacia o no responde"
despues="$(docker exec "$CONTENEDOR_DB" psql -U maryun -d postgres -tAc \
    "SELECT pg_size_pretty(pg_database_size('$DESTINO'))")"
log "  base copiada: $despues, $filas documentos del SII"

# ──────────────────────────────────────────────────────────── el bucket ──
[ -r "$CONFIG_ORIGEN" ] || morir "falta $CONFIG_ORIGEN"
[ -r "$CONFIG_PREVIEW" ] || morir "falta $CONFIG_PREVIEW"
# shellcheck disable=SC1090
. "$CONFIG_ORIGEN"
# shellcheck disable=SC1090
. "$CONFIG_PREVIEW"
for v in R2_ENDPOINT R2_ORIGEN_BUCKET R2_ORIGEN_KEY_ID R2_ORIGEN_SECRET \
         PREVIEW_R2_ENDPOINT PREVIEW_R2_BUCKET PREVIEW_R2_KEY_ID PREVIEW_R2_SECRET; do
    [ -n "${!v:-}" ] || morir "falta $v en los .env"
done
[ "$PREVIEW_R2_BUCKET" != "$R2_ORIGEN_BUCKET" ] \
    || morir "el bucket de preview y el de produccion son el mismo: $PREVIEW_R2_BUCKET"

salida="$(docker run --rm \
    -e RCLONE_CONFIG_PROD_TYPE=s3 -e RCLONE_CONFIG_PROD_PROVIDER=Cloudflare \
    -e RCLONE_CONFIG_PROD_ENDPOINT="$R2_ENDPOINT" \
    -e RCLONE_CONFIG_PROD_ACCESS_KEY_ID="$R2_ORIGEN_KEY_ID" \
    -e RCLONE_CONFIG_PROD_SECRET_ACCESS_KEY="$R2_ORIGEN_SECRET" \
    -e RCLONE_CONFIG_PREV_TYPE=s3 -e RCLONE_CONFIG_PREV_PROVIDER=Cloudflare \
    -e RCLONE_CONFIG_PREV_ENDPOINT="$PREVIEW_R2_ENDPOINT" \
    -e RCLONE_CONFIG_PREV_ACCESS_KEY_ID="$PREVIEW_R2_KEY_ID" \
    -e RCLONE_CONFIG_PREV_SECRET_ACCESS_KEY="$PREVIEW_R2_SECRET" \
    rclone/rclone:latest sync \
      "prod:$R2_ORIGEN_BUCKET" "prev:$PREVIEW_R2_BUCKET" \
      --checksum --transfers 8 --stats-one-line --stats 1m 2>&1)"
codigo=$?
salida="$(printf '%s\n' "$salida" | grep -v 'NOTICE: Config file')"
[ -n "$salida" ] && printf '%s\n' "$salida" | tail -3 | while read -r l; do log "    $l"; done
[ "$codigo" -eq 0 ] || morir "fallo el espejo del bucket a preview"
log "  bucket sincronizado a $PREVIEW_R2_BUCKET"

# ──────────────────────────────────── reiniciar la aplicacion de preview ──
# Se busca por su DATABASE_URL y no por nombre: Coolify le pone un sufijo que
# cambia en cada despliegue.
APP=""
for c in $(docker ps --format '{{.Names}}'); do
    u="$(docker exec "$c" printenv DATABASE_URL 2>/dev/null)"
    case "$u" in *"$DESTINO"*) APP="$c"; break;; esac
done
if [ -n "$APP" ]; then
    # Su entrypoint aplica las migraciones pendientes al arrancar: si la rama de
    # preview tiene migraciones que produccion no tiene, se vuelven a aplicar
    # sobre los datos recien copiados.
    docker restart "$APP" >/dev/null && log "  aplicacion de preview reiniciada ($APP)" \
        || log "  AVISO: no se pudo reiniciar $APP"
else
    log "  AVISO: no se encontro el contenedor de preview; reinicialo a mano"
fi

log "=== refresco terminado"
