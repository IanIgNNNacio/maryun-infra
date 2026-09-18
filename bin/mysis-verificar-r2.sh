#!/usr/bin/env bash
#
# Comprueba, archivo por archivo, que lo que hay en R2 es lo que hay en el disco
# de MySis. Se corre EN LA VM DE AZURE.
#
#   sudo /root/mysis-verificar-r2.sh              comprueba por hash (lo bueno)
#   sudo /root/mysis-verificar-r2.sh --rapido     comprueba solo tamaños
#
# ── Por qué existe ──────────────────────────────────────────────────────────
#
# Porque unos 222.900 de los PDF anteriores a 2022 no tienen token en
# `mstr_pedidos_token` y su XML firmado no está ni en la base ni en disco. Para
# ese 27 % del corpus, **el archivo de REPO es la única copia que posee Maryun**,
# y son documentos tributarios que el SII puede requerir durante seis años.
#
# Así que la regla es: NO SE BORRA NADA DEL ORIGEN hasta que esto pase limpio.
# Y «pasa limpio» no es «rclone dijo que subió 943.720»: es comparar el
# contenido, porque un objeto puede existir en R2 con cero bytes o truncado y el
# recuento seguiría cuadrando.
#
# ── LO QUE CUESTA, medido el 18-sep-2026 ────────────────────────────────────
#
# `--checksum` calcula el MD5 de CADA ARCHIVO LOCAL, o sea que lee los 123 GiB
# enteros del disco. Con 64 comparadores en paralelo sobre un directorio plano
# de 943.720 entradas, la máquina de Azure subió a **carga 32 sobre 4 núcleos**.
#
# Eso está bien un sábado sin nadie dentro. NO SE CORRE UN LUNES.
#
# Para repetirla en horario —y hay que repetirla después del corte, porque
# entrarán archivos nuevos— usa `--rapido`, que compara sólo tamaños y no lee un
# solo byte. Es menos seguro, pero detecta igual lo que de verdad pasa: objetos
# que faltan y objetos truncados. La comprobación por hash completa se hace una
# vez, la primera, que es ésta.
#
# ── Por qué se puede comparar por hash ──────────────────────────────────────
#
# Ningún archivo de REPO pasa de 1 MB, así que todos subieron en una sola pieza
# y el ETag que devuelve R2 es el MD5 del contenido. Con subidas multiparte el
# ETag no es un MD5 y `rclone check` tendría que descargar para comparar; aquí
# no hace falta. Es lo que hace viable comprobar 943.720 objetos sin bajar
# 123 GiB.
set -euo pipefail

ENV=/root/mysis-r2.env
ORIGEN=/var/www/html/mryn
INFORME=/var/log/mysis-r2-verificacion.log

[ -r "$ENV" ] || { echo "falta $ENV"; exit 2; }
# shellcheck disable=SC1090
. "$ENV"

MODO=(--checksum)
ETIQUETA="por hash"
if [ "${1:-}" = "--rapido" ]; then
  MODO=(--size-only)
  ETIQUETA="solo tamaños (rápido, menos seguro)"
fi

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
echo " verificación R2  ·  $ETIQUETA  ·  $(date -Is)"
echo "=================================================================="

TOTAL_MAL=0

for d in "${DIRS[@]}"; do
  [ -d "$ORIGEN/$d" ] || continue
  echo
  echo "--- $d"

  # Los que faltan y los que difieren van a archivos aparte, para poder
  # resubirlos con `rclone copy --files-from` sin volver a recorrer el árbol.
  FALTAN="/tmp/r2-faltan-$(echo "$d" | tr / _).txt"
  DIFIEREN="/tmp/r2-difieren-$(echo "$d" | tr / _).txt"

  # `check` no copia nada: sólo compara. --differ y --missing-on-dst escriben
  # las listas; --one-way porque en R2 puede haber cosas que aquí ya no estén y
  # eso no es un error (el corpus es append-only, pero los uploads sí se borran).
  set +e
  rc check "$ORIGEN/$d" "r2:$MYSIS_R2_BUCKET/$d" \
      "${MODO[@]}" \
      --one-way \
      --missing-on-dst "$FALTAN" \
      --differ "$DIFIEREN" \
      --checkers 64 \
      --stats 2m \
      --stats-one-line \
      --stats-log-level NOTICE \
      --log-level NOTICE
  codigo=$?
  set -e

  nf=$( [ -f "$FALTAN" ]   && wc -l < "$FALTAN"   || echo 0 )
  nd=$( [ -f "$DIFIEREN" ] && wc -l < "$DIFIEREN" || echo 0 )
  TOTAL_MAL=$(( TOTAL_MAL + nf + nd ))

  printf '    faltan en R2: %s\n    difieren:     %s\n' "$nf" "$nd"
  [ "$nf" -gt 0 ] && echo "    -> resubir con: rclone copy $ORIGEN/$d r2:$MYSIS_R2_BUCKET/$d --files-from $FALTAN"
  [ "$nd" -gt 0 ] && echo "    -> REVISAR A MANO antes de resubir: $DIFIEREN"
  [ "$codigo" -ne 0 ] && echo "    (rclone salió con código $codigo)"
done

echo
echo "=================================================================="
if [ "$TOTAL_MAL" -eq 0 ]; then
  echo " TODO CUADRA. Se puede considerar R2 como copia buena."
  echo
  echo " Aun así, y esto no es una formalidad: antes de borrar nada del origen"
  echo " conviene una copia fría fuera de R2. Un bucket no es un respaldo si un"
  echo " borrado por error se replica solo."
else
  echo " HAY $TOTAL_MAL ARCHIVOS QUE NO CUADRAN. NO BORRAR NADA DEL ORIGEN."
fi
echo " $(date -Is)"
echo "=================================================================="
