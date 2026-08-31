#!/usr/bin/env bash
#
# Respaldo de maryun01 — capas 1 y 2.
#
#   Capa 1  Volcados logicos: PostgreSQL (6 bases) y ClickHouse (3 bases).
#   Capa 2  Lo que no esta en ningun git: los pipelines de Mage, la
#           configuracion de Coolify y /srv/secrets (cifrado).
#
# La copia FUERA del servidor es la capa 3 y va en otro script: esto deja todo
# preparado en /srv/backups, que vive en el mismo RAID y por tanto NO es un
# respaldo de verdad todavia.
#
# Idempotente. Cada corrida crea su propio directorio con sello de tiempo.
# Uso:  respaldo.sh [--retencion-dias N]

set -uo pipefail

DESTINO=/srv/backups
DISCO_CH="$DESTINO/clickhouse"          # el disco que exige ClickHouse
SELLO="$(date -u '+%Y-%m-%dT%H%M')"
DIR="$DESTINO/$SELLO"
LOG=/var/log/maryun-respaldo.log
CLAVE_AGE=/srv/secrets/respaldo-age.pub
RETENCION_DIAS=14
FALLOS=0

while [ $# -gt 0 ]; do
    case "$1" in
        --retencion-dias) RETENCION_DIAS="$2"; shift 2;;
        *) echo "opcion desconocida: $1"; exit 2;;
    esac
done

log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }
fallo() { log "FALLO: $*"; FALLOS=$((FALLOS + 1)); }

mkdir -p "$DIR"/{postgres,clickhouse,archivos,secretos}

log "=== respaldo $SELLO ==="

# ─────────────────────────────────────────────────────── capa 1: PostgreSQL ──
# Formato custom (-Fc): comprimido y restaurable tabla por tabla con pg_restore.
postgres_bases() {
    local contenedor="$1" usuario
    usuario="$(docker exec "$contenedor" printenv POSTGRES_USER 2>/dev/null)" || return 1
    [ -z "$usuario" ] && return 1
    docker exec "$contenedor" psql -U "$usuario" -d postgres -tAc \
        "SELECT datname FROM pg_database WHERE datistemplate = false AND datname <> 'postgres';" 2>/dev/null
}

for contenedor in maryun-erp-db coolify-db mage-db metabase-db superset-db; do
    docker inspect "$contenedor" >/dev/null 2>&1 || { log "  $contenedor no existe, se omite"; continue; }
    usuario="$(docker exec "$contenedor" printenv POSTGRES_USER 2>/dev/null)"
    for base in $(postgres_bases "$contenedor"); do
        salida="$DIR/postgres/${contenedor}--${base}.dump"
        if docker exec "$contenedor" pg_dump -U "$usuario" -Fc -d "$base" > "$salida" 2>/dev/null \
           && [ -s "$salida" ]; then
            log "  postgres  $contenedor/$base  $(du -h "$salida" | cut -f1)"
        else
            fallo "postgres $contenedor/$base"
            rm -f "$salida"
        fi
    done
done

# ─────────────────────────────────────────────────────── capa 1: ClickHouse ──
# El comando BACKUP nativo toma una instantanea consistente. Copiar los archivos
# de /var/lib/clickhouse en caliente puede dejar un estado a medias.
ch() {
    . /srv/secrets/clickhouse.env
    docker exec clickhouse clickhouse-client \
        --user admin --password "$CLICKHOUSE_ADMIN_PASSWORD" "$@"
}

if docker inspect clickhouse >/dev/null 2>&1; then
    mkdir -p "$DISCO_CH/$SELLO"
    chown 101:101 "$DISCO_CH/$SELLO"
    for base in dwh logistica logistica_v2; do
        if ch --query "BACKUP DATABASE ${base} TO Disk('backups', '${SELLO}/${base}.zip')" >/dev/null 2>&1; then
            archivo="$DISCO_CH/$SELLO/${base}.zip"
            if [ -s "$archivo" ]; then
                mv "$archivo" "$DIR/clickhouse/"
                log "  clickhouse  $base  $(du -h "$DIR/clickhouse/${base}.zip" | cut -f1)"
            else
                fallo "clickhouse $base (archivo vacio)"
            fi
        else
            fallo "clickhouse $base"
        fi
    done
    rmdir "$DISCO_CH/$SELLO" 2>/dev/null || true
fi

# ──────────────────────────────────── capa 2: lo que no esta en ningun git ──
# Los 45 MB de pipelines de Mage son lo mas irreemplazable del servidor: no
# existen en ningun repositorio.
archivar() {
    local nombre="$1" origen="$2"
    [ -e "$origen" ] || { log "  $origen no existe, se omite"; return 0; }
    local salida="$DIR/archivos/${nombre}.tar.gz"
    # En GNU tar los --exclude van ANTES de las rutas: puestos despues los
    # ignora y termina con estado de error aunque el archivo se haya creado.
    if tar --exclude='__pycache__' --exclude='*.pyc' \
         -czf "$salida" -C "$(dirname "$origen")" "$(basename "$origen")" 2>/dev/null; then
        log "  archivos  $nombre  $(du -h "$salida" | cut -f1)"
    else
        fallo "archivo $nombre"
        rm -f "$salida"
    fi
}

archivar mage-pipelines  /srv/stacks/mage/project
archivar coolify-config  /data/coolify
archivar clickhouse-conf /srv/stacks/clickhouse/config.d

# ──────────────────────────────────────── capa 2: secretos, siempre cifrados ──
# Se cifran con la clave PUBLICA de age: el servidor puede cifrar pero NO
# descifrar. La privada la guarda Ian fuera del servidor. Asi un respaldo
# robado no entrega las credenciales.
if [ -r "$CLAVE_AGE" ] && command -v age >/dev/null 2>&1; then
    salida="$DIR/secretos/srv-secrets.tar.gz.age"
    if tar --exclude='respaldo-age.key' -czf - -C /srv secrets 2>/dev/null \
         | age -r "$(cat "$CLAVE_AGE")" -o "$salida" 2>/dev/null && [ -s "$salida" ]; then
        log "  secretos  cifrado con age  $(du -h "$salida" | cut -f1)"
    else
        fallo "secretos (cifrado)"
        rm -f "$salida"
    fi
else
    fallo "secretos: falta $CLAVE_AGE o el binario age"
fi

# ──────────────────────────────────────────────────────────────  manifiesto ──
# Sin sumas de verificacion no se puede demostrar que un respaldo llego intacto.
( cd "$DIR" && find . -type f ! -name MANIFIESTO.txt -exec sha256sum {} + \
    | sort -k2 > MANIFIESTO.txt )
log "  manifiesto  $(grep -c . "$DIR/MANIFIESTO.txt") archivos"

chown -R root:maryun "$DIR"
chmod -R g+rX,o-rwx "$DIR"
chmod 640 "$DIR/secretos/"* 2>/dev/null || true

# ─────────────────────────────────────────────────────────────── retencion ──
borrados=0
for viejo in "$DESTINO"/20*T*; do
    [ -d "$viejo" ] || continue
    [ "$viejo" = "$DIR" ] && continue
    if [ -n "$(find "$viejo" -maxdepth 0 -mtime "+$RETENCION_DIAS" 2>/dev/null)" ]; then
        rm -rf "$viejo"; borrados=$((borrados + 1))
    fi
done
[ "$borrados" -gt 0 ] && log "  retencion  $borrados respaldos de mas de $RETENCION_DIAS dias eliminados"

# ────────────────────────────────────────────────────────────────── cierre ──
total="$(du -sh "$DIR" | cut -f1)"
if [ "$FALLOS" -eq 0 ]; then
    log "=== OK: $total en $DIR ==="
    exit 0
fi
log "=== TERMINADO CON $FALLOS FALLO(S): $total en $DIR ==="
exit 1
