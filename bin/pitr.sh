#!/usr/bin/env bash
#
# PITR de la base del ERP — recuperacion a un instante cualquiera.
#
# Que resuelve. Hasta ahora el unico respaldo de maryun_erp era el volcado
# diario de las 03:15 UTC, o sea una ventana de perdida de hasta 24 horas. El
# ERP emite unos 3.100 documentos al SII cada dia y un DTE aceptado no se puede
# des-enviar: restaurar al volcado de ayer deja la base sin facturas que el SII
# si tiene, con folios quemados que la base cree libres. Eso se arregla a mano,
# folio por folio. Con PITR la ventana baja a un minuto (archive_timeout=60).
#
# Como esta montado:
#
#   archive_command  --->  /var/lib/pgbackrest  (= /srv/pitr en el anfitrion)
#                                |
#                                +--(rclone crypt, cada 15 min)--->  R2
#
# El repositorio local es el unico que toca archive_command. Es deliberado: si
# el archivado dependiera de la red y la red fallara, PostgreSQL dejaria de
# reciclar WAL, pg_wal creceria hasta llenar el disco y el motor se detendria.
# Es el modo de fallo clasico de PITR y aqui no puede ocurrir por un problema
# de red.
#
# La copia de fuera va cifrada con la misma passphrase que el respaldo diario
# (RCLONE_CRYPT_PASSWORD, documentada en /srv/secrets/RECUPERACION.txt).
#
# Uso:
#   pitr.sh crear      crea la stanza (una sola vez) y comprueba el archivado
#   pitr.sh full       respaldo completo
#   pitr.sh diff       respaldo diferencial
#   pitr.sh check      comprueba que el archivado funciona de verdad
#   pitr.sh info       que hay en el repositorio y hasta donde se puede volver
#   pitr.sh externo    empuja el repositorio a R2 y aplica retencion alla
#   pitr.sh ensayo [ "2026-09-04 01:00:00" ]
#                      restaura de verdad en una instancia aparte y verifica.
#                      No toca la base de produccion. Sin fecha, restaura hasta
#                      el final del WAL archivado. Con LIMPIAR=1 delante no deja
#                      rastro: es lo que hace el temporizador mensual.

set -uo pipefail

CONTENEDOR=maryun-erp-db
STANZA=maryun
REPO=/srv/pitr
LOG=/var/log/maryun-pitr.log
CONFIG_R2=/srv/secrets/r2-respaldo.env
CONFIG_OVH=/srv/secrets/ovh-backup.env
RETENCION_EXTERNA_DIAS=30

log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }

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

morir() { log "ERROR: $*"; telegram "PITR maryun01 - $*"; exit 1; }

pgbr() { docker exec -u postgres "$CONTENEDOR" pgbackrest --stanza="$STANZA" "$@"; }

# Un solo ciclo a la vez: dos respaldos simultaneos se pisan.
exec 9>/var/lock/maryun-pitr.lock
flock -n 9 || { log "ya hay una operacion de PITR en marcha, salgo"; exit 0; }

docker inspect "$CONTENEDOR" >/dev/null 2>&1 || morir "el contenedor $CONTENEDOR no existe"

case "${1:-}" in

crear)
    log "=== stanza-create"
    pgbr stanza-create || morir "stanza-create fallo"
    pgbr check || morir "check fallo: el archivado no esta funcionando"
    log "=== stanza lista"
    ;;

full|diff|incr)
    tipo="$1"
    log "=== respaldo $tipo"
    if pgbr backup --type="$tipo"; then
        log "=== respaldo $tipo OK"
    else
        morir "el respaldo $tipo fallo"
    fi
    ;;

check)
    pgbr check || morir "check fallo: el archivado no esta funcionando"
    ;;

info)
    pgbr info
    echo
    echo "--- tamano del repositorio local"
    du -sh "$REPO"
    ;;

externo)
    # --- la copia fuera del servidor -------------------------------------
    [ -r "$CONFIG_R2" ] || morir "falta $CONFIG_R2"
    [ -r "$CONFIG_OVH" ] || morir "falta $CONFIG_OVH"
    # shellcheck disable=SC1090
    . "$CONFIG_R2"
    # shellcheck disable=SC1090
    . "$CONFIG_OVH"
    for v in R2_ENDPOINT R2_RESPALDO_BUCKET R2_RESPALDO_KEY_ID R2_RESPALDO_SECRET \
             RCLONE_CRYPT_PASSWORD; do
        [ -n "${!v:-}" ] || morir "falta $v en los .env"
    done

    PASS_OBS="$(docker run --rm rclone/rclone:latest obscure "$RCLONE_CRYPT_PASSWORD" | tr -d '\r\n')"
    [ -n "$PASS_OBS" ] || morir "no se pudo ofuscar la passphrase"

    rc() {
        docker run --rm \
            -v "$REPO":/repo:ro \
            -e RCLONE_CONFIG_R2_TYPE=s3 \
            -e RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
            -e RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT" \
            -e RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_RESPALDO_KEY_ID" \
            -e RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_RESPALDO_SECRET" \
            -e RCLONE_CONFIG_CIFRADO_TYPE=crypt \
            -e RCLONE_CONFIG_CIFRADO_REMOTE="r2:${R2_RESPALDO_BUCKET}/pitr" \
            -e RCLONE_CONFIG_CIFRADO_PASSWORD="$PASS_OBS" \
            -e RCLONE_CONFIG_CIFRADO_FILENAME_ENCRYPTION=standard \
            rclone/rclone:latest "$@" 2>&1
    }

    log "=== copia del repositorio a R2"
    # `copy`, nunca `sync`: si alguien borra el repositorio local, el borrado no
    # se propaga fuera. La retencion de alla se aplica aparte, por antiguedad.
    # Se excluye el enlace simbolico "latest": rclone no lo puede seguir y
    # pgbackrest no lo necesita para restaurar, porque la lista de respaldos
    # esta en backup.info. Sin la exclusion cada corrida deja un aviso inutil.
    if rc copy /repo cifrado: --exclude "backup/*/latest" \
              --transfers 8 --checkers 16 --stats-one-line --stats 1m; then
        log "  copia OK"
    else
        morir "la copia a R2 fallo"
    fi

    # Marca de tiempo para que vigilar-pitr.sh sepa cuando salio la ultima copia.
    date -Is > /var/log/maryun-pitr-externo.stamp

    log "=== retencion externa (${RETENCION_EXTERNA_DIAS} dias)"
    rc delete cifrado: --min-age "${RETENCION_EXTERNA_DIAS}d" --rmdirs \
        || log "  aviso: la retencion externa no termino bien"
    ;;

ensayo)
    # --- restauracion de verdad, en una instancia aparte ------------------
    # Un respaldo que no se ha probado a restaurar no es un respaldo. Esto
    # levanta un PostgreSQL nuevo desde el repositorio y consulta datos reales.
    # La base de produccion no se toca: el repositorio se monta de solo lectura.
    OBJETIVO="${2:-}"
    TRABAJO=/srv/pitr-ensayo
    CONT=pitr-ensayo
    PUERTO=5439

    docker rm -f "$CONT" >/dev/null 2>&1 || true
    rm -rf "$TRABAJO"
    # 999 es el uid/gid de postgres dentro del contenedor. `install -o 999` falla
    # cuando ese uid no tiene nombre en el anfitrion; con mkdir+chown no.
    mkdir -p "$TRABAJO"
    chown 999:999 "$TRABAJO"
    chmod 0700 "$TRABAJO"

    IMAGEN="$(docker inspect "$CONTENEDOR" --format '{{.Config.Image}}')"

    log "=== restaurando en $TRABAJO"
    if [ -n "$OBJETIVO" ]; then
        log "    objetivo: $OBJETIVO (hora de America/Santiago)"
        EXTRA=(--type=time --target="$OBJETIVO" --target-action=promote)
    else
        log "    objetivo: el final del WAL archivado"
        EXTRA=(--type=default)
    fi

    docker run --rm -u postgres \
        -v "$REPO":/var/lib/pgbackrest:ro \
        -v /srv/stacks/maryun-erp/pgbackrest:/etc/pgbackrest:ro \
        -v "$TRABAJO":/destino \
        --entrypoint pgbackrest "$IMAGEN" \
        --stanza="$STANZA" --pg1-path=/destino --log-path=/tmp \
        restore "${EXTRA[@]}" \
        || morir "la restauracion fallo"

    # Se salta el entrypoint de la imagen oficial a proposito: mira $PGDATA
    # (/var/lib/postgresql/18/docker), lo ve vacio y exige POSTGRES_PASSWORD.
    # Aqui los datos restaurados estan en /destino y ya vienen inicializados.
    log "=== arrancando la instancia de ensayo en el puerto $PUERTO"
    docker run -d --name "$CONT" -u postgres \
        -e TZ=America/Santiago \
        -v "$REPO":/var/lib/pgbackrest:ro \
        -v /srv/stacks/maryun-erp/pgbackrest:/etc/pgbackrest:ro \
        -v "$TRABAJO":/destino \
        -p 127.0.0.1:${PUERTO}:5432 \
        --entrypoint postgres \
        "$IMAGEN" -D /destino \
        -c archive_mode=off \
        -c max_connections=200 \
        >/dev/null || morir "no se pudo arrancar la instancia de ensayo"
    # max_connections=200 no es un detalle: PostgreSQL se niega a recuperar si
    # la instancia que restaura tiene menos plazas que la que genero el WAL
    # ("recovery aborted because of insufficient parameter settings"). Lo mismo
    # valdria para max_worker_processes, max_locks_per_transaction y
    # max_prepared_transactions si alguna vez se suben en produccion.
    # restore_command lo escribe el propio pgbackrest en postgresql.auto.conf
    # al restaurar; no hace falta pasarlo aqui.

    log "    esperando a que termine de recuperar..."
    for _ in $(seq 1 90); do
        docker exec "$CONT" pg_isready -U maryun -d postgres >/dev/null 2>&1 && break
        sleep 2
    done

    docker exec "$CONT" pg_isready -U maryun -d postgres >/dev/null 2>&1 \
        || { docker logs --tail 40 "$CONT"; morir "la instancia de ensayo no acepto conexiones"; }

    log "=== verificacion sobre la instancia restaurada"
    DOCS="$(docker exec "$CONT" psql -U maryun -d maryun_erp -tAc \
        "select count(*) from \"SiiDocument\"" 2>/dev/null)"
    RELOJ="$(docker exec "$CONT" psql -U maryun -d postgres -tAc "select now()" 2>/dev/null)"
    log "    documentos del SII en la copia restaurada: ${DOCS:-ERROR}"
    log "    reloj de la instancia restaurada: ${RELOJ:-ERROR}"

    if [ -z "$DOCS" ] || [ "$DOCS" -lt 1 ] 2>/dev/null; then
        docker rm -f "$CONT" >/dev/null 2>&1
        morir "el ensayo restauro, pero la copia no tiene datos del ERP"
    fi

    if [ "${LIMPIAR:-0}" = "1" ]; then
        # Modo automatico (temporizador mensual): comprobar y no dejar rastro.
        docker rm -f "$CONT" >/dev/null 2>&1
        rm -rf "$TRABAJO"
        log "=== ensayo mensual OK: la restauracion funciona ($DOCS documentos)"
    else
        log "=== la instancia de ensayo quedo viva en 127.0.0.1:$PUERTO"
        log "    inspeccionarla:  docker exec -it $CONT psql -U maryun -d maryun_erp"
        log "    terminarla:      docker rm -f $CONT && sudo rm -rf $TRABAJO"
    fi
    ;;

*)
    sed -n '/^# Uso:/,/^$/p' "$0" | sed 's/^#\{1\} \{0,1\}//'
    exit 2
    ;;
esac
