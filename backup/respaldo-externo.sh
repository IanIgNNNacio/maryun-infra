#!/usr/bin/env bash
#
# Respaldo de maryun01 — capa 3: la copia FUERA del servidor.
#
# Empuja el ultimo respaldo local al Backup Storage de OVHcloud (500 GB
# incluidos con el servidor dedicado), CIFRADO en origen con la capa `crypt`
# de rclone.
#
# Por que cifrado: los volcados de base de datos van en claro. Sin cifrar,
# OVHcloud podria leer los datos de negocio y una credencial robada del backup
# storage entregaria todo. Con `crypt` se cifran contenidos Y nombres de
# archivo antes de salir del servidor.
#
#   SIN LA CLAVE DE CIFRADO ESTE RESPALDO ES IRRECUPERABLE.
#   Vive en /srv/secrets/RECUPERACION.txt y Ian debe tener una copia fuera.
#
# No monta nada: empuja y se desconecta. Un NFS montado de forma permanente
# permitiria que un servidor comprometido borrara los respaldos.
#
# Usa `copy`, nunca `sync`: un borrado local no se propaga al destino.
#
# Uso:  respaldo-externo.sh [--retencion-dias N]

set -uo pipefail

ORIGEN=/srv/backups
CONFIG=/srv/secrets/ovh-backup.env
LOG=/var/log/maryun-respaldo.log
RETENCION_DIAS=30
FALLOS=0

while [ $# -gt 0 ]; do
    case "$1" in
        --retencion-dias) RETENCION_DIAS="$2"; shift 2;;
        *) echo "opcion desconocida: $1"; exit 2;;
    esac
done

log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }

[ -r "$CONFIG" ] || { log "externo: falta $CONFIG"; exit 1; }
# shellcheck disable=SC1090
. "$CONFIG"

for v in OVH_HOST OVH_USER OVH_PASS RCLONE_CRYPT_PASSWORD; do
    [ -n "${!v:-}" ] || { log "externo: falta $v en $CONFIG"; exit 1; }
done

# rclone exige las contrasenas "ofuscadas", no en claro.
obscure() {
    docker run --rm rclone/rclone:latest obscure "$1" 2>/dev/null | tr -d '\r\n'
}

PASS_OBS="$(obscure "$OVH_PASS")"
CRYPT_OBS="$(obscure "$RCLONE_CRYPT_PASSWORD")"

[ -n "$PASS_OBS" ] && [ -n "$CRYPT_OBS" ] || { log "externo: no se pudo ofuscar las contrasenas"; exit 1; }

# El remoto "ovh" habla FTP con TLS explicito; "cifrado" lo envuelve con crypt.
rc() {
    docker run --rm \
        -v "$ORIGEN":/datos:ro \
        -e RCLONE_CONFIG_OVH_TYPE=ftp \
        -e RCLONE_CONFIG_OVH_HOST="$OVH_HOST" \
        -e RCLONE_CONFIG_OVH_USER="$OVH_USER" \
        -e RCLONE_CONFIG_OVH_PASS="$PASS_OBS" \
        -e RCLONE_CONFIG_OVH_EXPLICIT_TLS="${OVH_TLS:-true}" \
        -e RCLONE_CONFIG_OVH_NO_CHECK_CERTIFICATE="${OVH_TLS_INSEGURO:-false}" \
        -e RCLONE_CONFIG_OVH_CONCURRENCY=2 \
        -e RCLONE_CONFIG_OVH_CLOSE_TIMEOUT=120s \
        -e RCLONE_CONFIG_CIFRADO_TYPE=crypt \
        -e RCLONE_CONFIG_CIFRADO_REMOTE="ovh:maryun01" \
        -e RCLONE_CONFIG_CIFRADO_PASSWORD="$CRYPT_OBS" \
        -e RCLONE_CONFIG_CIFRADO_FILENAME_ENCRYPTION=standard \
        rclone/rclone:latest "$@" 2>&1
}

# ─────────────────────────────────────────────────── el respaldo mas reciente ──
ULTIMO="$(find "$ORIGEN" -maxdepth 1 -type d -name '20*T*' | sort | tail -1)"
[ -n "$ULTIMO" ] || { log "externo: no hay respaldos locales que subir"; exit 1; }
SELLO="$(basename "$ULTIMO")"

log "externo: subiendo $SELLO ($(du -sh "$ULTIMO" | cut -f1)) cifrado a OVH"

if rc copy "/datos/$SELLO" "cifrado:$SELLO" --transfers 2 --checkers 1 --stats-one-line --stats 30s | tail -3 | while read -r l; do log "  $l"; done; then
    :
fi

# ──────────────────────────────────────────────────────────── verificacion ──
# `check` compara tamanos y sumas: sin esto no se sabe si llego completo.
if rc check "/datos/$SELLO" "cifrado:$SELLO" --one-way --checkers 1 2>&1 | grep -qiE "0 differences|no differences"; then
    log "externo: verificado, $SELLO llego completo"
else
    salida="$(rc check "/datos/$SELLO" "cifrado:$SELLO" --one-way --checkers 1 2>&1 | tail -2)"
    log "externo: FALLO la verificacion de $SELLO"
    log "  $salida"
    FALLOS=$((FALLOS + 1))
fi

# ─────────────────────────────────────────────────────────────── retencion ──
# En el destino se guarda mas tiempo que en local: es la copia que sobrevive.
borrados=0
while read -r remoto; do
    [ -z "$remoto" ] && continue
    nombre="${remoto%/}"
    fecha="${nombre:0:10}"
    if [ -n "$fecha" ] && [ "$(date -d "$fecha" +%s 2>/dev/null || echo 0)" -lt \
         "$(date -d "-$RETENCION_DIAS days" +%s)" ]; then
        rc purge "cifrado:$nombre" >/dev/null 2>&1 && borrados=$((borrados + 1))
    fi
done < <(rc lsf cifrado: --dirs-only 2>/dev/null)
[ "$borrados" -gt 0 ] && log "externo: $borrados respaldos de mas de $RETENCION_DIAS dias eliminados del destino"

# ────────────────────────────────────────────────────────────────── cierre ──
uso="$(rc size cifrado: --json --checkers 1 2>/dev/null | tr -d '{}" ' | tr ',' ' ')"
log "externo: uso en OVH -> ${uso:-desconocido}"

[ "$FALLOS" -eq 0 ] && { log "externo: OK"; exit 0; }
log "externo: TERMINADO CON $FALLOS FALLO(S)"
exit 1
