#!/usr/bin/env bash
#
# Respaldo diario de maryun01: encadena las tres capas y avisa el resultado.
#
#   1. respaldo.sh          volcados locales en /srv/backups
#   2. respaldo-externo.sh  copia cifrada al Backup Storage de OVH
#   3. aviso a Uptime Kuma  para que un fallo no pase inadvertido
#
# El aviso importa tanto como el respaldo: sin el, un respaldo que lleva un mes
# fallando parece uno que funciona. Kuma tambien alerta si este script deja de
# avisar (monitor de tipo push, ventana de 26 h), asi que cubre el caso de que
# el temporizador ni siquiera llegue a ejecutarse.

set -uo pipefail

LOG=/var/log/maryun-respaldo.log
KUMA=http://10.8.0.1:3001/api/push
. /srv/secrets/monitoreo.env 2>/dev/null || true

log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }

avisar() {   # avisar <up|down> <mensaje>
    [ -n "${KUMA_TOKEN_RESPALDO:-}" ] || { log "diario: sin token de Kuma, no se avisa"; return; }
    curl -fsS --max-time 20 --get "$KUMA/$KUMA_TOKEN_RESPALDO" \
        --data-urlencode "status=$1" \
        --data-urlencode "msg=$2" >/dev/null 2>&1 \
        && log "diario: avisado a Kuma ($1)" \
        || log "diario: no se pudo avisar a Kuma"
}

inicio=$(date +%s)
fallos=""

log "=== respaldo diario: inicio ==="

if /srv/bin/respaldo.sh; then
    log "diario: capa local OK"
else
    fallos="local"
    log "diario: FALLO la capa local"
fi

# La copia externa se intenta igual: un volcado parcial fuera del servidor vale
# mas que ninguno, y el aviso dira que algo fallo.
if /srv/bin/respaldo-externo.sh; then
    log "diario: copia externa OK"
else
    fallos="${fallos:+$fallos y }externa"
    log "diario: FALLO la copia externa"
fi

dur=$(( ($(date +%s) - inicio) / 60 ))
tam="$(du -sh "$(find /srv/backups -maxdepth 1 -type d -name '20*T*' | sort | tail -1)" 2>/dev/null | cut -f1)"

if [ -z "$fallos" ]; then
    log "=== respaldo diario OK (${tam:-?} en ${dur} min) ==="
    avisar up "OK ${tam:-?} en ${dur} min"
    exit 0
fi
log "=== respaldo diario CON FALLOS: $fallos ==="
avisar down "fallo la capa $fallos"
exit 1
