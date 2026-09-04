#!/usr/bin/env bash
#
# Copia los adjuntos del ERP a un bucket de respaldo.
#
#   maryun-erp  ──(rclone copy)──►  maryun-erp-respaldo
#
# Es lo mismo que respaldo.sh hace con las bases, pero para los archivos: XML
# del SII, respaldos de nomina, imagenes del catalogo. Nada de lo que pase con
# la base cubre esto: un volcado de PostgreSQL devuelve filas, no devuelve un
# objeto borrado de un bucket.
#
# Portado del VPS viejo (/opt/maryun-erp/scripts/replica/archivos.sh) el
# 1-sep-2026. Tres cambios respecto al original:
#
#  1. Las credenciales viven en /srv/secrets/, como el resto de este servidor,
#     en vez de /etc/maryun/.
#  2. Avisa a Uptime Kuma al terminar. En el VPS llevaba fallando desde la
#     rotacion de los tokens de R2 -SignatureDoesNotMatch- y nadie se entero,
#     porque escribia el error en un log que nadie miraba. Un respaldo que falla
#     en silencio es peor que no tenerlo.
#  3. Corregida la nota final: R2 NO tiene versionado de objetos. Tiene
#     bloqueos de bucket, que para este caso son mejores.
#
# Uso:  archivos.sh

set -uo pipefail

CONFIG=/srv/secrets/r2-respaldo.env
LOG=/var/log/maryun-archivos.log
KUMA=http://10.8.0.1:3001/api/push

log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }

avisar() {   # avisar <up|down> <mensaje>
    [ -n "${KUMA_TOKEN_ARCHIVOS:-}" ] || return 0
    curl -fsS --max-time 20 --get "$KUMA/$KUMA_TOKEN_ARCHIVOS" \
        --data-urlencode "status=$1" --data-urlencode "msg=$2" >/dev/null 2>&1 \
        && log "  avisado a Kuma ($1)" || log "  no se pudo avisar a Kuma"
}

morir() { log "ERROR: $*"; avisar down "$*"; exit 1; }

# Un solo ciclo a la vez.
exec 9>/tmp/maryun-archivos.lock
flock -n 9 || { log "ya hay una copia en marcha, salgo"; exit 0; }

[ -r "$CONFIG" ] || { log "ERROR: no puedo leer $CONFIG"; exit 1; }
# shellcheck disable=SC1090
. "$CONFIG"
. /srv/secrets/monitoreo.env 2>/dev/null || true

for v in R2_ENDPOINT R2_ORIGEN_BUCKET R2_ORIGEN_KEY_ID R2_ORIGEN_SECRET \
         R2_RESPALDO_BUCKET R2_RESPALDO_KEY_ID R2_RESPALDO_SECRET; do
    [ -n "${!v:-}" ] || morir "falta $v en $CONFIG"
done

# rclone se configura por variables de entorno, no por archivo: asi las
# credenciales no quedan escritas en ningun sitio salvo el .env, que esta en 0640.
rc() {
    docker run --rm \
        -e RCLONE_CONFIG_ORIGEN_TYPE=s3 \
        -e RCLONE_CONFIG_ORIGEN_PROVIDER=Cloudflare \
        -e RCLONE_CONFIG_ORIGEN_ENDPOINT="$R2_ENDPOINT" \
        -e RCLONE_CONFIG_ORIGEN_ACCESS_KEY_ID="$R2_ORIGEN_KEY_ID" \
        -e RCLONE_CONFIG_ORIGEN_SECRET_ACCESS_KEY="$R2_ORIGEN_SECRET" \
        -e RCLONE_CONFIG_RESPALDO_TYPE=s3 \
        -e RCLONE_CONFIG_RESPALDO_PROVIDER=Cloudflare \
        -e RCLONE_CONFIG_RESPALDO_ENDPOINT="$R2_ENDPOINT" \
        -e RCLONE_CONFIG_RESPALDO_ACCESS_KEY_ID="$R2_RESPALDO_KEY_ID" \
        -e RCLONE_CONFIG_RESPALDO_SECRET_ACCESS_KEY="$R2_RESPALDO_SECRET" \
        rclone/rclone:latest "$@" 2>&1
}

log "=== copiando $R2_ORIGEN_BUCKET -> $R2_RESPALDO_BUCKET ==="

# `copy`, NUNCA `sync`.
#
# `sync` deja el destino identico al origen, y eso incluye borrar lo que ya no
# este — convirtiendo el respaldo en un espejo del borrado que vino a evitar.
# `copy` solo agrega y actualiza. Son cuatro letras de diferencia y es toda la
# diferencia.
#
# --checksum compara por suma en vez de por fecha: R2 no siempre conserva la
# fecha de modificacion al copiar entre buckets.
# El destino lleva un prefijo propio, erp/, y NO la raiz del bucket.
#
# Por que un prefijo: desde el 4-sep-2026 el PITR de la base guarda en este
# mismo bucket bajo pitr/. Contando el bucket entero, esos objetos inflaban la
# cuenta y el guardarrail de mas abajo comparaba adjuntos contra
# adjuntos-mas-PITR: podia faltar la mitad de los adjuntos y seguir dando OK.
#
# Por que "erp" y no "adjuntos": el bucket de origen no guarda solo adjuntos.
# El ERP separa por ESPACIO -"adjuntos" de facturas y nominas, "productos" del
# catalogo- y cada espacio es un prefijo dentro del bucket. Copiando el bucket
# entero bajo un prefijo llamado adjuntos/ quedaba adjuntos/adjuntos/facturas/,
# que ademas de feo era mentira en cuanto apareciera otro espacio.
DESTINO="respaldo:$R2_RESPALDO_BUCKET/erp"

salida="$(rc copy "origen:$R2_ORIGEN_BUCKET" "$DESTINO" \
          --checksum --transfers 8 --stats-one-line --stats 1m)"
codigo=$?
echo "$salida" | tail -4 | while read -r l; do log "  $l"; done
[ "$codigo" -eq 0 ] || morir "fallo la copia: $(echo "$salida" | grep -oE 'api error [A-Za-z]+' | head -1)"

# ── verificacion ───────────────────────────────────────────────────────────
# El destino puede tener MAS objetos que el origen, y eso es correcto: conserva
# lo que se borro en produccion. Lo que no puede pasar es que tenga MENOS.
contar() {
    rc size "$1" --json 2>/dev/null | tr -d '{}" ' | tr ',' '\n' | grep '^count:' | cut -d: -f2
}
ORIGEN_N="$(contar "origen:$R2_ORIGEN_BUCKET")"
RESPALDO_N="$(contar "$DESTINO")"
log "  origen: ${ORIGEN_N:-?} objetos · respaldo/erp: ${RESPALDO_N:-?}"

if [ -n "$ORIGEN_N" ] && [ -n "$RESPALDO_N" ] && [ "$RESPALDO_N" -lt "$ORIGEN_N" ]; then
    morir "el respaldo tiene MENOS objetos que el origen: la copia quedo incompleta"
fi

# Con el origen a 0 objetos no hay nada que verificar, y decir "OK N
# objetos" seria enganoso: hasta el 4-sep-2026 ese N contaba los archivos
# del PITR, que no son adjuntos. Mientras el ERP este en desarrollo y no
# haya subido nada, el estado correcto es verde con el mensaje claro: un
# monitor en rojo por algo esperado se acaba ignorando, y entonces no avisa
# de lo que si importa. El guardarrail de arriba es el que protege de una
# copia incompleta en cuanto empiecen a existir adjuntos.
if [ "${ORIGEN_N:-0}" = "0" ]; then
    log "=== el origen esta vacio: no hay adjuntos que respaldar todavia ==="
    log "    el ERP escribe en $R2_ORIGEN_BUCKET (STORAGE_DRIVER=r2)"
    avisar up "origen vacio: 0 adjuntos, nada que copiar"
    exit 0
fi

log "=== copia terminada: ${RESPALDO_N:-?} adjuntos en el respaldo ==="
avisar up "OK ${RESPALDO_N:-?} adjuntos"
exit 0

# ── NOTA sobre la proteccion del bucket de respaldo ─────────────────────────
#
# R2 no ofrece un permiso de "escribir sin borrar", asi que el token de destino
# PUEDE borrar. La proteccion real NO es el versionado: R2 no tiene versionado
# de objetos (verificado en su documentacion; el script original decia lo
# contrario). Lo que tiene son BLOQUEOS DE BUCKET, que son mejores para este
# caso: impiden borrar y sobrescribir durante un plazo definido, prevalecen
# sobre las reglas de ciclo de vida, y un bucket con bloqueo activo no se puede
# vaciar — ni siquiera por quien tenga las credenciales.
#
# Se configura en: R2 -> bucket -> Settings -> Bucket lock rules.
