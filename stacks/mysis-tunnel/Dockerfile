# Tunel SSH persistente hacia MySis (MariaDB en la VM de Azure).
#
# Las dependencias se instalan al CONSTRUIR, no al arrancar. En el VPS viejo
# el contenedor hacia "apk add openssh-client autossh" en cada reinicio: si los
# repositorios de Alpine no respondian en ese momento, el tunel no levantaba y
# el ETL se quedaba sin fuente de datos.
FROM alpine:3.22

RUN apk add --no-cache openssh-client autossh

# Para el healthcheck: comprueba que el puerto reenviado realmente acepta
# conexiones, no solo que el proceso de autossh sigue vivo.
RUN apk add --no-cache netcat-openbsd

ENTRYPOINT ["autossh", "-M", "0", "-N"]
