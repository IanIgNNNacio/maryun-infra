# MySis: Apache + PHP 7.4, congelados.
#
# PHP 7.4.33 lleva sin soporte desde noviembre de 2022 y Ubuntu moderno trae 8.3.
# No hay paquete que instalar en el anfitrion, asi que va aqui dentro. Eso no es
# una concesion: es lo que permite mover la maquina AHORA y subir la version
# DESPUES, que es el orden que pidio Ian. Mezclar una mudanza con un cambio de
# lenguaje es como se pierde el rastro de que rompio que.
#
# La etiqueta va clavada a proposito -no `7.4-apache` a secas- para que una
# reconstruccion dentro de seis meses de exactamente esta imagen.
FROM php:7.4.33-apache-bullseye

# Las extensiones que produccion tiene cargadas y esta imagen NO trae de serie.
# Salieron de `php -m` en la VM de Azure, no de suponer. El resto -curl, dom,
# mbstring, openssl, session, SimpleXML, xml, zip...- ya viene.
#
# Las de la familia sysv* y shmop estan porque `php -m` las lista; no se ha
# encontrado quien las use, pero quitarlas seria descubrirlo el lunes.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libfreetype6-dev libjpeg62-turbo-dev libpng-dev \
        libicu-dev libxml2-dev libxslt1-dev libzip-dev \
        ; \
    docker-php-ext-configure gd --with-freetype --with-jpeg; \
    docker-php-ext-install -j"$(nproc)" \
        gd intl mysqli pdo_mysql xsl zip exif sockets calendar \
        pcntl shmop sysvmsg sysvsem sysvshm ftp gettext opcache \
        ; \
    apt-get purge -y --auto-remove libfreetype6-dev libjpeg62-turbo-dev \
        libpng-dev libicu-dev libxml2-dev libxslt1-dev libzip-dev; \
    rm -rf /var/lib/apt/lists/*

# Los modulos de Apache que produccion tiene cargados vienen todos activos en
# esta imagen salvo dos, y esos dos NO se activan aqui: `mod_rewrite` y
# `mod_proxy` no estan en Azure, y encenderlos cambiaria el comportamiento del
# sistema durante la mudanza. Si el diseno de R2 acaba necesitandolos, se
# encienden en su propio cambio, con su propia prueba.

# `deflate` si esta activo en produccion y aqui tambien, que es lo que hace que
# un listado de 2.000 filas no viaje en crudo.
RUN a2enmod deflate

# El php.ini de produccion no es el de la imagen. Lo importante:
#
#   date.timezone  — en Azure NO ESTA PUESTO, y PHP lo adivina como
#                    America/Halifax. Los cron calculan ventanas de fechas con
#                    esa zona. Aqui se fija America/Santiago EXPLICITAMENTE: si
#                    se dejara adivinar, la adivinanza cambiaria de maquina y
#                    las fechas se correrian en silencio, que es la peor forma
#                    de romperse.
#
#   memory_limit   — produccion tiene -1 (sin limite) en CLI. Aqui se pone un
#                    numero: sin limite, una pagina que se desboque se lleva por
#                    delante al resto del servidor, que ahora comparte maquina
#                    con el ERP y las bases.
#
#   opcache        — en Azure esta cargado. Sin el, PHP recompila cada peticion.
COPY php-produccion.ini /usr/local/etc/php/conf.d/zz-produccion.ini

# La aplicacion se monta desde el anfitrion, no se copia a la imagen: son 1,5 GB
# que cambian sin pasar por aqui, y ademas asi un despliegue es un `git pull` y
# no una reconstruccion.
WORKDIR /var/www/html
