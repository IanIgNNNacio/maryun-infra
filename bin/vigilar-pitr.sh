#!/usr/bin/env bash
#
# Vigilancia del PITR. Esto NO es opcional: es lo que hace que el riesgo del
# archivado continuo sea manejable.
#
# El modo de fallo que hay que cazar: si archive_command falla, PostgreSQL deja
# de reciclar los segmentos de WAL para no perderlos. pg_wal crece sin parar
# hasta llenar el disco, y con el disco lleno el motor se detiene. Es decir, mal
# vigilado, el mecanismo que protege los datos es el que tumba la base.
#
# Se comprueba:
#   1. que el archivado no este fallando         (pg_stat_archiver)
#   2. cuanto WAL queda sin archivar             (LSN actual vs ultimo archivado)
#   3. el tamano de pg_wal                       (el sintoma de que se atasco)
#   4. el espacio libre en /srv
#   5. la edad del ultimo respaldo completo/diferencial
#   6. la edad de la ultima copia fuera del servidor
#
# Avisa por Telegram, con enfriamiento para no repetir el mismo aviso cada vez.
#
# Uso:  vigilar-pitr.sh [--verboso]

set -uo pipefail

CONTENEDOR=maryun-erp-db
STANZA=maryun
LOG=/var/log/maryun-pitr.log
ESTADO=/var/lib/maryun-pitr-avisos
STAMP_EXTERNO=/var/log/maryun-pitr-externo.stamp
ENFRIAMIENTO=$((6 * 3600))          # no repetir el mismo aviso antes de 6 h

# Umbrales
WAL_PENDIENTE_MB=256                # WAL generado y aun sin archivar
PGWAL_MB=4096                       # tamano de pg_wal: 2x max_wal_size + margen
DISCO_LIBRE_PCT=15
RESPALDO_HORAS=36
EXTERNO_HORAS=6

VERBOSO=0
[ "${1:-}" = "--verboso" ] && VERBOSO=1

log() { printf '[%s] %s\n' "$(date -Is)" "$*" >> "$LOG"; }
di()  { [ "$VERBOSO" = 1 ] && echo "$*"; log "$*"; }

mkdir -p "$ESTADO"

telegram() {
    . /srv/secrets/monitoreo.env 2>/dev/null || true
    [ -n "${TELEGRAM_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT:-}" ] || return 0
    # Los mensajes traen %0A como separador, igual que el resto de /srv/bin.
    # Aqui se pasa a salto real: --data-urlencode codifica lo que reciba, y sin
    # esta conversion el usuario veria un literal "%0A" en el mensaje.
    local texto="${1//%0A/$'\n'}"
    curl -s -m 15 -o /dev/null \
        --data "chat_id=$TELEGRAM_CHAT" \
        --data "parse_mode=HTML" \
        --data-urlencode "text=$texto" \
        "https://api.telegram.org/bot$TELEGRAM_TOKEN/sendMessage" || true
}

# avisar <clave> <mensaje>  — respeta el enfriamiento por clave
avisar() {
    local clave="$1" mensaje="$2" marca="$ESTADO/$1" ahora
    ahora=$(date +%s)
    if [ -f "$marca" ] && [ $((ahora - $(cat "$marca"))) -lt "$ENFRIAMIENTO" ]; then
        log "  ($clave en enfriamiento) $mensaje"
        return 0
    fi
    echo "$ahora" > "$marca"
    log "  AVISO $clave: $mensaje"
    telegram "<b>PITR maryun01</b>%0A%0A$mensaje"
    PROBLEMAS=$((PROBLEMAS + 1))
}

# resuelto <clave> — borra la marca para que el proximo fallo avise de inmediato
resuelto() { rm -f "$ESTADO/$1"; }

PROBLEMAS=0

sql() { docker exec "$CONTENEDOR" psql -U maryun -d postgres -tAc "$1" 2>/dev/null; }

docker inspect "$CONTENEDOR" >/dev/null 2>&1 || {
    avisar contenedor "El contenedor $CONTENEDOR no existe. No hay archivado de WAL."
    exit 1
}

# ─────────────────────────────────────────── 1. el archivado esta fallando? ──
LEIDO="$(sql "select coalesce(archived_count,0)||'|'||coalesce(failed_count,0)||'|'||coalesce(last_archived_wal,'-')||'|'||coalesce(last_failed_wal,'-')||'|'||coalesce(extract(epoch from now()-last_failed_time)::int::text,'-1') from pg_stat_archiver")"
[ -n "$LEIDO" ] || avisar sin-estadisticas "No se pudo leer pg_stat_archiver. La vigilancia del PITR esta ciega."
IFS='|' read -r ARCHIVADOS FALLIDOS ULT_OK ULT_MAL EDAD_FALLO <<< "$LEIDO"

di "archivados=$ARCHIVADOS fallidos=$FALLIDOS ultimo_ok=$ULT_OK ultimo_fallo=$ULT_MAL"

if [ "${EDAD_FALLO:--1}" != "-1" ] && [ "$EDAD_FALLO" -lt 900 ]; then
    avisar archivado-falla \
      "El archivado de WAL esta FALLANDO.%0AUltimo fallo hace ${EDAD_FALLO}s en $ULT_MAL.%0AFallos acumulados: $FALLIDOS.%0A%0ASi no se arregla, pg_wal crece hasta llenar el disco y PostgreSQL se detiene.%0A%0Asudo /srv/bin/pitr.sh check"
else
    resuelto archivado-falla
fi

# ────────────────────────────── 2. cuanto WAL hay generado y sin archivar? ──
# El nombre de un segmento son tres campos hexadecimales de 8 digitos:
# linea de tiempo, logid y numero de segmento. Con segmentos de 16 MB hay 256
# por logid, asi que el byte donde empieza el ultimo archivado es
# (logid*256 + segmento) * 16777216. La diferencia con el LSN actual es lo que
# se ha escrito y todavia no ha salido hacia el repositorio.
PENDIENTE_MB="$(sql "
  select ((pg_wal_lsn_diff(pg_current_wal_lsn(),'0/0')
           - ( ('x'||substr(last_archived_wal, 9,8))::bit(32)::bigint::numeric * 256
             + ('x'||substr(last_archived_wal,17,8))::bit(32)::bigint::numeric ) * 16777216
          ) / 1048576)::bigint
  from pg_stat_archiver
  where last_archived_wal is not null")"
di "wal pendiente de archivar: ${PENDIENTE_MB:-sin dato} MB"
if [ -n "$PENDIENTE_MB" ] && [ "$PENDIENTE_MB" -gt "$WAL_PENDIENTE_MB" ] 2>/dev/null; then
    avisar wal-pendiente \
      "Hay ${PENDIENTE_MB} MB de WAL generados y sin archivar (umbral ${WAL_PENDIENTE_MB} MB).%0AEl archivado va por detras de la escritura."
else
    resuelto wal-pendiente
fi

# ─────────────────────────────────── 3. tamano de pg_wal: el sintoma grave ──
PGWAL="$(docker exec "$CONTENEDOR" sh -c 'du -sm $PGDATA/pg_wal 2>/dev/null | cut -f1')"
di "pg_wal: ${PGWAL:-?} MB"
if [ -n "$PGWAL" ] && [ "$PGWAL" -gt "$PGWAL_MB" ] 2>/dev/null; then
    avisar pgwal-grande \
      "pg_wal ocupa ${PGWAL} MB (umbral ${PGWAL_MB} MB).%0AEsto es lo que pasa cuando el archivado se atasca: el WAL no se recicla.%0ASi llega a llenar /srv, PostgreSQL se detiene."
else
    resuelto pgwal-grande
fi

# ─────────────────────────────────────────────────── 4. espacio libre en /srv ──
LIBRE_PCT="$(df --output=pcent /srv | tail -1 | tr -dc '0-9')"
LIBRE_PCT=$((100 - LIBRE_PCT))
di "/srv libre: ${LIBRE_PCT}%"
if [ "$LIBRE_PCT" -lt "$DISCO_LIBRE_PCT" ]; then
    avisar disco "Solo queda ${LIBRE_PCT}% libre en /srv (umbral ${DISCO_LIBRE_PCT}%).%0AEl repositorio de PITR y pg_wal viven ahi."
else
    resuelto disco
fi

# ──────────────────────────────────── 5. edad del ultimo respaldo en el repo ──
INFO="$(docker exec -u postgres "$CONTENEDOR" pgbackrest --stanza="$STANZA" --output=json info 2>/dev/null)"
if [ -z "$INFO" ] || [ "$INFO" = "[]" ]; then
    avisar repo-vacio "El repositorio de PITR no responde o esta vacio.%0Asudo /srv/bin/pitr.sh info"
else
    ULTIMO_TS="$(printf '%s' "$INFO" | python3 -c 'import json,sys
d=json.load(sys.stdin)
b=[x for s in d for x in s.get("backup",[])]
print(max((x["timestamp"]["stop"] for x in b), default=0))' 2>/dev/null)"
    if [ -n "$ULTIMO_TS" ] && [ "$ULTIMO_TS" != "0" ]; then
        HORAS=$(( ( $(date +%s) - ULTIMO_TS ) / 3600 ))
        di "ultimo respaldo en el repo: hace ${HORAS} h"
        if [ "$HORAS" -gt "$RESPALDO_HORAS" ]; then
            avisar respaldo-viejo \
              "El ultimo respaldo del repositorio tiene ${HORAS} h (umbral ${RESPALDO_HORAS} h).%0ASin una copia base reciente, restaurar exige reproducir mucho mas WAL."
        else
            resuelto respaldo-viejo
        fi
    else
        avisar sin-respaldo "El repositorio de PITR no tiene ningun respaldo completo todavia.%0Asudo /srv/bin/pitr.sh full"
    fi
fi

# ────────────────────────────── 6. edad de la ultima copia fuera del servidor ──
if [ -f "$STAMP_EXTERNO" ]; then
    HORAS_EXT=$(( ( $(date +%s) - $(date -d "$(cat "$STAMP_EXTERNO")" +%s) ) / 3600 ))
    di "ultima copia a R2: hace ${HORAS_EXT} h"
    if [ "$HORAS_EXT" -gt "$EXTERNO_HORAS" ]; then
        avisar externo-viejo \
          "La ultima copia del PITR a R2 tiene ${HORAS_EXT} h (umbral ${EXTERNO_HORAS} h).%0AEl repositorio local sigue bien, pero ahora mismo solo existe dentro del servidor."
    else
        resuelto externo-viejo
    fi
else
    di "aun no hay marca de copia externa"
fi

if [ "$PROBLEMAS" -eq 0 ]; then
    di "todo en orden"
    exit 0
fi
exit 1
