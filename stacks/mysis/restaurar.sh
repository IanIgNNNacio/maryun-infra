#!/usr/bin/env bash
# Restaura el volcado de MySis en el contenedor de OVH.
# El volcado es el respaldo nocturno del propio Azure -mysqldump --opt del
# 2026-09-18 02:33-, 168 tablas y 8,35 GB en crudo. No se volco otra vez porque
# nadie ha tocado el sistema desde entonces: comprobado, cero archivos nuevos en
# 24 h.
set -euo pipefail
. /srv/secrets/mysis-db.env
echo "inicio: $(date -Is)"
zcat /srv/traspaso/mysis/my-20260918.gz \
  | docker exec -i -e MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mysis-db \
      mariadb -u root --default-character-set=utf8mb3 mryn_data
echo "fin: $(date -Is)"
