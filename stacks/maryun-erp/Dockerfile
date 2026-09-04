# Postgres del ERP + pgBackRest.
#
# Por que una imagen propia: archive_command lo ejecuta el propio motor, dentro
# del contenedor. pgBackRest tiene que estar ahi dentro; no sirve un contenedor
# aparte.
#
# La imagen base se fija a una version exacta a proposito. Al subir de version
# de Postgres hay que cambiar las dos: aqui y en docker-compose.yml.
FROM postgres:18.6

# pgbackrest sale del repositorio PGDG que la imagen oficial ya trae
# configurado, asi que la version acompana a la de Postgres.
RUN apt-get update \
 && apt-get install -y --no-install-recommends pgbackrest \
 && rm -rf /var/lib/apt/lists/* \
 && pgbackrest version

# El repositorio y los registros son volumenes; se crean aqui con el
# propietario correcto para que existan aunque se monten vacios.
RUN install -d -o postgres -g postgres -m 0750 /var/lib/pgbackrest /var/log/pgbackrest
