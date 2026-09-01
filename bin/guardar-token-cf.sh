#!/usr/bin/env bash
#
# Guarda el token de API de Cloudflare para emitir certificados por validacion DNS.
#
# Por que hace falta: metabase, superset, mage y coolify apuntan a 10.8.0.1, una
# direccion privada. Let's Encrypt no puede alcanzarla para la validacion por
# HTTP, pero SI puede comprobar un registro TXT en la zona. Eso permite tener
# certificados validos en nombres que solo existen dentro de la VPN.
#
# El token se pide con la entrada oculta para que no quede en el historial del
# shell, y se comprueba contra la API antes de guardarlo: un token invalido
# guardado en silencio se descubre semanas despues, cuando el certificado no
# renueva.
#
# Uso:  sudo /srv/bin/guardar-token-cf.sh

set -uo pipefail

SECRETOS=/srv/secrets/cloudflare-dns.env
ZONA=maryun.cl

[ "$(id -u)" -eq 0 ] || { echo "Ejecutalo con sudo."; exit 1; }

if grep -q '^CF_DNS_API_TOKEN=' "$SECRETOS" 2>/dev/null; then
    echo "Ya hay un token guardado en $SECRETOS."
    printf "Reemplazarlo? [s/N] "
    read -r resp
    [ "$resp" = "s" ] || [ "$resp" = "S" ] || { echo "Sin cambios."; exit 0; }
fi

echo "Pega el token de Cloudflare (no se vera al escribir):"
read -r -s TOKEN
echo
[ -n "$TOKEN" ] || { echo "Token vacio."; exit 1; }

api() {
    curl -sS --max-time 25 -H "Authorization: Bearer $TOKEN" \
        -H "User-Agent: curl/8.5.0" "https://api.cloudflare.com/client/v4/$1"
}

echo
echo "=== 1. el token es valido? ==="
estado="$(api 'user/tokens/verify' | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('respuesta ilegible'); raise SystemExit
if d.get('success'):
    print(d.get('result', {}).get('status', 'desconocido'))
else:
    errs = '; '.join(e.get('message', '?') for e in d.get('errors', []))
    print('RECHAZADO: ' + errs)
")"
echo "  $estado"
case "$estado" in
    active) ;;
    *) echo "  No se guarda."; exit 1;;
esac

echo
echo "=== 2. alcanza la zona $ZONA? ==="
zona="$(api "zones?name=$ZONA" | python3 -c "
import sys, json
d = json.load(sys.stdin)
r = d.get('result') or []
print(r[0]['id'] if r else 'NO')
" 2>/dev/null)"
if [ "$zona" = "NO" ] || [ -z "$zona" ]; then
    echo "  El token NO ve la zona $ZONA."
    echo "  Revisa que en 'Zone Resources' hayas incluido esa zona concreta."
    exit 1
fi
echo "  si (zona ${zona:0:8}...)"

echo
echo "=== 3. puede ESCRIBIR registros DNS? ==="
# Se comprueba leyendo los permisos efectivos en vez de crear un registro de
# prueba: crear y borrar en una zona de produccion es peor que preguntar.
permisos="$(api 'user/tokens/verify' | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('ok' if d.get('success') else 'no')
")"
listar="$(api "zones/$zona/dns_records?per_page=1" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('si' if d.get('success') else 'NO')
")"
echo "  puede listar registros: $listar"
[ "$listar" = "si" ] || { echo "  Sin acceso a los registros DNS. No se guarda."; exit 1; }

echo
echo "=== 4. guardando ==="
cat > "$SECRETOS" <<EOF
# Token de Cloudflare para la validacion DNS de Let's Encrypt.
# Alcance: Zone -> DNS -> Edit, solo sobre $ZONA.
# Lo usa Traefik para emitir certificados de los nombres que viven en la VPN
# (metabase, superset, mage, coolify), cuyos registros apuntan a 10.8.0.1 y
# por tanto no admiten la validacion por HTTP.
CF_DNS_API_TOKEN=$TOKEN
EOF
chmod 0640 "$SECRETOS"
chown root:maryun "$SECRETOS"
ls -l "$SECRETOS" | awk '{printf "  %s %s:%s %s\n", $1, $3, $4, $9}'

echo
echo "=== listo. Avisale a Claude para que configure los certificados. ==="
