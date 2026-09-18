#!/usr/bin/env bash
#
# Sube a R2 los archivos de datos de MySis, desde la propia VM de Azure.
#
#   sudo /root/subir-a-r2.sh            arranca o continua
#   tail -f /var/log/mysis-r2.log       ver como va
#
# POR QUE DESDE AQUI Y NO PASANDO POR OVH: Azure cobra la salida de datos. Un
# solo salto Azure -> R2 la cobra una vez; Azure -> OVH -> R2 la cobraria igual
# y ademas tardaria el doble. Medido en esta maquina: 67 MB/s de subida hacia
# Cloudflare, asi que la banda no es el limite - lo es el numero de objetos.
#
# LA DISPOSICION DE LAS CLAVES ES LA MISMA RUTA. `REPO/2024/05/algo.pdf` queda
# en `REPO/2024/05/algo.pdf`. No es falta de imaginacion: cualquier cambio en el
# PHP va a necesitar una correspondencia deterministica entre la ruta de hoy y
# la clave de R2, y la identidad es la unica que no hay que documentar ni
# mantener. Si mas adelante conviene otra, se renombra en R2, que es barato.
#
# ES REANUDABLE. `rclone copy` compara antes de subir, asi que si esto se corta
# -o si se vuelve a correr el lunes para recoger lo que entro entretanto- sube
# solo lo que falta. Por eso es `copy` y no `sync`: `sync` BORRA en el destino
# lo que no este en el origen, y aqui el destino va a ser la copia buena.
#
# NO TOCA NADA DEL ORIGEN. Solo lee.
set -euo pipefail

ENV=/root/mysis-r2.env
LOG=/var/log/mysis-r2.log
ORIGEN=/var/www/html/mryn

[ -r "$ENV" ] || { echo "falta $ENV"; exit 2; }
# shellcheck disable=SC1090
. "$ENV"

# Los directorios de datos, del mas grande al mas chico. El orden importa poco
# salvo para que el grande arranque primero y se vea avanzar.
DIRS=(REPO pages/rendicion pages/mail pages/solicitud pages/salidas_excel)

rc() {
  RCLONE_CONFIG_R2_TYPE=s3 \
  RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
  RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT" \
  RCLONE_CONFIG_R2_ACCESS_KEY_ID="$MYSIS_R2_KEY_ID" \
  RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$MYSIS_R2_SECRET" \
  RCLONE_S3_NO_CHECK_BUCKET=true \
  /usr/local/bin/rclone "$@"
}

echo "=================================================================="
echo " subida a R2  ·  $(date -Is)"
echo "=================================================================="

for d in "${DIRS[@]}"; do
  [ -d "$ORIGEN/$d" ] || { echo "-- $d no existe, me lo salto"; continue; }
  echo
  echo "--- $d"
  # --transfers y --checkers altos porque son cientos de miles de archivos
  # pequenos: lo que manda es el viaje de ida y vuelta por objeto, no el ancho
  # de banda. 64 en una maquina de 4 nucleos es agresivo pero el cuello esta en
  # la red, no en la CPU.
  #
  # --no-update-modtime evita un PUT extra de metadatos por archivo ya subido.
  # --retries-sleep da aire si R2 empieza a limitar.
  rc copy "$ORIGEN/$d" "r2:$MYSIS_R2_BUCKET/$d" \
      --transfers 64 \
      --checkers 64 \
      --no-update-modtime \
      --retries 5 \
      --retries-sleep 10s \
      --low-level-retries 20 \
      --stats 2m \
      --stats-one-line \
      --stats-log-level NOTICE \
      --log-level INFO \
      --exclude '.*' \
      || echo "!! $d termino con errores, revisar arriba"
done

echo
echo "=================================================================="
echo " recuento final  ·  $(date -Is)"
for d in "${DIRS[@]}"; do
  [ -d "$ORIGEN/$d" ] || continue
  printf '  %-22s local: ' "$d"
  find "$ORIGEN/$d" -type f 2>/dev/null | wc -l | tr -d '\n'
  printf '  en R2: '
  rc size "r2:$MYSIS_R2_BUCKET/$d" 2>/dev/null | tr '\n' ' '
  echo
done
echo " terminado  ·  $(date -Is)"
echo "=================================================================="
