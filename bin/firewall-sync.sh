#!/bin/bash
# firewall-sync.sh — sincroniza las reglas de firewall de maryun01.
#
# Hace dos cosas:
#   1. Refresca los rangos de Cloudflare permitidos en UFW (puertos 80/443 del HOST).
#   2. Reconstruye la cadena DOCKER-USER para que Docker NO se salte el firewall.
#
# Por defecto NINGUN contenedor es alcanzable desde internet. La unica excepcion
# son los puertos 80 y 443 desde los rangos de Cloudflare, para Traefik.
#
# Idempotente: se puede correr las veces que sea.
set -euo pipefail

CF_V4_URL="https://www.cloudflare.com/ips-v4"
CF_V6_URL="https://www.cloudflare.com/ips-v6"
CACHE_DIR="/srv/stacks/traefik"
LOG_TAG="firewall-sync"

log() { echo "[$(date -Is)] $*"; logger -t "$LOG_TAG" "$*" 2>/dev/null || true; }

WAN="$(ip -o -4 route show default | awk '{print $5}' | head -1)"
if [ -z "$WAN" ]; then
    log "ERROR: no se pudo detectar la interfaz WAN"
    exit 1
fi
log "interfaz WAN detectada: $WAN"

# ---------------------------------------------------------------- rangos CF --
# Se cachean en disco: si Cloudflare no responde, se usa la ultima copia buena
# en vez de dejar el firewall sin reglas.
fetch_cf() {
    local url="$1" cache="$2"
    if curl -fsS --max-time 20 "$url" -o "${cache}.tmp" && [ -s "${cache}.tmp" ]; then
        mv "${cache}.tmp" "$cache"
        log "rangos actualizados desde $url"
    else
        rm -f "${cache}.tmp"
        if [ -s "$cache" ]; then
            log "AVISO: no se pudo consultar $url, se usa la cache existente"
        else
            log "ERROR: no se pudo consultar $url y no hay cache. Abortando."
            exit 1
        fi
    fi
}

mkdir -p "$CACHE_DIR"
fetch_cf "$CF_V4_URL" "$CACHE_DIR/cloudflare-ips-v4.txt"
fetch_cf "$CF_V6_URL" "$CACHE_DIR/cloudflare-ips-v6.txt"

CF_V4="$(grep -E '^[0-9]' "$CACHE_DIR/cloudflare-ips-v4.txt" || true)"
CF_V6="$(grep -E '^[0-9a-fA-F]*:' "$CACHE_DIR/cloudflare-ips-v6.txt" || true)"

# ------------------------------------------------------------ UFW (el host) --
# Se borran solo las reglas comentadas como Cloudflare y se vuelven a crear.
sync_ufw() {
    local removed=0
    while :; do
        local num
        num="$(ufw status numbered 2>/dev/null | grep -m1 'Cloudflare' | sed -E 's/^\[[[:space:]]*([0-9]+)\].*/\1/' || true)"
        [ -z "$num" ] && break
        yes | ufw delete "$num" >/dev/null 2>&1 || break
        removed=$((removed + 1))
    done
    log "UFW: $removed reglas antiguas de Cloudflare eliminadas"

    local added=0
    for ip in $CF_V4 $CF_V6; do
        ufw allow proto tcp from "$ip" to any port 80,443 comment 'Cloudflare' >/dev/null
        added=$((added + 1))
    done
    log "UFW: $added reglas de Cloudflare creadas"
}

# -------------------------------------------------------- DOCKER-USER (Docker) --
# Docker escribe sus reglas ANTES que las de UFW, asi que 'ufw deny' no protege
# los puertos publicados por contenedores. DOCKER-USER es el gancho oficial.
sync_docker_user() {
    local ipt="$1"; shift
    local cf_list="$*"

    "$ipt" -N DOCKER-USER 2>/dev/null || true
    "$ipt" -F DOCKER-USER

    # 1. Respuestas a conexiones que iniciamos nosotros: siempre pasan.
    "$ipt" -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN

    # 2. Redes privadas (VPN WireGuard, redes internas de Docker, LAN).
    if [ "$ipt" = "iptables" ]; then
        "$ipt" -A DOCKER-USER -s 10.0.0.0/8     -j RETURN
        "$ipt" -A DOCKER-USER -s 172.16.0.0/12  -j RETURN
        "$ipt" -A DOCKER-USER -s 192.168.0.0/16 -j RETURN
    else
        "$ipt" -A DOCKER-USER -s fc00::/7 -j RETURN
    fi

    # 3. Cloudflare hacia 80/443 (Traefik).
    local n=0
    for ip in $cf_list; do
        "$ipt" -A DOCKER-USER -s "$ip" -p tcp -m multiport --dports 80,443 -j RETURN
        n=$((n + 1))
    done

    # 4. Todo lo demas que entre por la WAN hacia un contenedor: se descarta.
    "$ipt" -A DOCKER-USER -i "$WAN" -j DROP

    # 5. El resto sigue su curso normal.
    "$ipt" -A DOCKER-USER -j RETURN

    log "$ipt: DOCKER-USER reconstruida ($n rangos de Cloudflare + DROP desde $WAN)"
}

sync_ufw
sync_docker_user iptables "$CF_V4"
if ip6tables -L DOCKER-USER -n >/dev/null 2>&1 || ip6tables -N DOCKER-USER 2>/dev/null; then
    sync_docker_user ip6tables "$CF_V6"
else
    log "AVISO: ip6tables sin cadena DOCKER-USER (IPv6 deshabilitado en Docker), se omite"
fi

log "sincronizacion completa"
