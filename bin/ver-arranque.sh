#!/bin/bash
# Muestra como arrancaron los contenedores del ERP: si aplicaron migraciones y
# cuales. Existe para no tener que escribir a mano un $(docker ps --filter ...)
# con comillas anidadas, que es donde se rompe siempre.
#
# Lo que hay que buscar despues de publicar es la linea:
#
#   Applying migration `20260910000000_canal_web_puntos_y_cuentas`
#
# Si en su lugar dice «No pending migrations to apply», la base ya estaba al dia
# -normal si se publica dos veces seguidas-. Si no aparece ninguna linea
# [arrancar], el contenedor es de una imagen anterior al arreglo.
set -uo pipefail

for app in maryun-erp-preview maryun-erp-produccion; do
  uuid=$(docker exec coolify-db psql -U coolify -d coolify -X -tA \
           -c "SELECT uuid FROM applications WHERE name = '$app'" 2>/dev/null | tr -d '[:space:]')
  if [ -z "$uuid" ]; then
    printf '\n== %s: no esta en Coolify\n' "$app"
    continue
  fi

  # --filter name= hace coincidencia parcial, y el contenedor se llama <uuid>-<algo>
  cont=$(docker ps --filter "name=$uuid" --format '{{.Names}}' | head -1)
  if [ -z "$cont" ]; then
    printf '\n== %s: sin contenedor vivo\n' "$app"
    continue
  fi

  img=$(docker inspect -f '{{.Config.Image}}' "$cont" 2>/dev/null)
  printf '\n== %s\n   contenedor: %s\n   imagen:     %s\n\n' "$app" "$cont" "${img##*:}"

  # El arranque escribe sus lineas al principio, antes de que Next empiece a
  # registrar peticiones, asi que las primeras 25 sobran de margen.
  docker logs "$cont" 2>&1 | head -25 \
    | grep -E '\[arrancar\]|Applying migration|No pending migrations|migrations found|Error|error:|Ready' \
    | sed 's/^/   /'

  if ! docker logs "$cont" 2>&1 | head -25 | grep -q '\[arrancar\]'; then
    printf '   (sin lineas [arrancar]: esta imagen es anterior al arreglo)\n'
  fi
done
echo
