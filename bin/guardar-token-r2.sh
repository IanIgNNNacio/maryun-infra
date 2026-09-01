#!/usr/bin/env bash
#
# Guarda el token de R2 del bucket de respaldo de adjuntos.
#
# archivos.sh copia maryun-erp -> maryun-erp-respaldo cada 6 horas. Las
# credenciales del bucket de ORIGEN ya estan puestas (son las mismas que usa el
# ERP). Falta el token del bucket de DESTINO, que necesita permiso de escritura.
#
# Se pide con la entrada oculta para que no quede en el historial del shell, y
# se comprueba contra R2 antes de guardarlo: puede LEER el bucket de respaldo y
# puede ESCRIBIR en el. Un token guardado sin comprobar se descubre roto seis
# horas despues, o peor, el dia que hagan falta los adjuntos.
#
# El token se crea en:
#   Cloudflare -> R2 -> API -> Manage API Tokens -> Create Token
#   Permisos:  Object Read & Write
#   Alcance:   solo el bucket  maryun-erp-respaldo
#
# Uso:  sudo /srv/bin/guardar-token-r2.sh

set -uo pipefail

SECRETOS=/srv/secrets/r2-respaldo.env

[ "$(id -u)" -eq 0 ] || { echo "Ejecutalo con sudo."; exit 1; }
[ -r "$SECRETOS" ] || { echo "falta $SECRETOS"; exit 1; }
. "$SECRETOS"

echo "Bucket de destino: ${R2_RESPALDO_BUCKET:-?}"
echo "Endpoint:          ${R2_ENDPOINT:-?}"
echo
echo "Access Key ID (no se vera al escribir):"
read -r -s KEY_ID
echo
echo "Secret Access Key (tampoco):"
read -r -s SECRET
echo

[ -n "$KEY_ID" ] && [ -n "$SECRET" ] || { echo "Falto uno de los dos."; exit 1; }
if [ "${#KEY_ID}" -ne 32 ]; then
    echo "El Access Key ID tiene ${#KEY_ID} caracteres y deberia tener 32."
    echo "Revisa que no se haya pegado a medias."
    exit 1
fi

rc() {
    docker run --rm \
        -e RCLONE_CONFIG_R2_TYPE=s3 \
        -e RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
        -e RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT" \
        -e RCLONE_CONFIG_R2_ACCESS_KEY_ID="$KEY_ID" \
        -e RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$SECRET" \
        rclone/rclone:latest "$@" 2>&1
}

echo "=== 1. puede LEER el bucket de respaldo? ==="
salida="$(rc lsd "r2:$R2_RESPALDO_BUCKET" 2>&1)"
if echo "$salida" | grep -qiE 'error|denied|InvalidArgument|SignatureDoesNotMatch'; then
    echo "  NO:"
    echo "$salida" | grep -oE 'api error [A-Za-z]+|[A-Z][a-zA-Z]*Denied' | head -2 | sed 's/^/    /'
    echo "  No se guarda."
    exit 1
fi
echo "  si"

echo
echo "=== 2. puede ESCRIBIR? (sube y borra un archivo de prueba) ==="
prueba="/tmp/.r2-prueba-$$"
echo "comprobacion de escritura $(date -Is)" > "$prueba"
if rc copyto "$prueba" "r2:$R2_RESPALDO_BUCKET/.comprobacion-acceso" 2>&1 | grep -qiE 'error|denied'; then
    echo "  NO puede escribir. Revisa que el token sea 'Object Read & Write'."
    rm -f "$prueba"; exit 1
fi
echo "  si"
rc delete "r2:$R2_RESPALDO_BUCKET/.comprobacion-acceso" >/dev/null 2>&1 && echo "  (archivo de prueba borrado)"
rm -f "$prueba"

echo
echo "=== 3. guardando ==="
cp -a "$SECRETOS" "$SECRETOS.bak-$(date +%F-%H%M)"
sed -i "s|^R2_RESPALDO_KEY_ID=.*|R2_RESPALDO_KEY_ID=$KEY_ID|" "$SECRETOS"
sed -i "s|^R2_RESPALDO_SECRET=.*|R2_RESPALDO_SECRET=$SECRET|" "$SECRETOS"
chmod 0640 "$SECRETOS"; chown root:maryun "$SECRETOS"
grep -c 'PENDIENTE' "$SECRETOS" | sed 's/^/  marcadores PENDIENTE que quedan: /'

echo
echo "=== 4. corrida de prueba de la copia completa ==="
/srv/bin/archivos.sh 2>&1 | tail -8 | sed 's/^/  /'
