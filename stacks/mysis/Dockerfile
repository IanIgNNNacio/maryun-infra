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

# LOS REPOSITORIOS DE bullseye YA NO EXISTEN donde apt los busca.
#
# Debian 11 llego al final de su vida y sus paquetes se movieron a
# archive.debian.org. Sin esto, la construccion muere con una pared de
# «404 Not Found» al pedir libicu67, y el mensaje no dice en ningun momento
# que el problema sea la EDAD de la distribucion. Costo un intento
# averiguarlo.
#
# Y SOLO el repositorio principal: para bullseye la rama de seguridad se llama
# `bullseye-security`, no `bullseye/updates` -esa era la nomenclatura de buster
# hacia atras-, y archive.debian.org no la publica. Pedirla da
# «does not have a Release file» y tumba la construccion entera. Todo lo que
# hace falta aqui esta en main.
#
# `Check-Valid-Until false` hace falta porque los indices del archivo llevan
# la fecha de caducidad pasada y apt se niega a usarlos aunque los descargue.
#
# ESTO ES EL PRECIO DE PHP 7.4, y conviene mirarlo de frente: la base de esta
# imagen ya NO RECIBE PARCHES DE SEGURIDAD, ni los va a recibir. Es aceptable
# porque el contenedor no se expone a internet -va detras de la VPN- y porque
# es temporal, pero es la razon numero uno para que la fase 5 no se quede en
# el cajon.
# `libssl-dev` no es para gd ni para intl: es para `ftp`. Esa extension lleva
# soporte TLS y sin las cabeceras de OpenSSL la compilacion muere con un
# «compilation terminated» en ftp.lo que no menciona OpenSSL por ninguna parte.
# Se descubrio compilando las diecisiete una por una; las otras dieciseis
# pasaban.
# NO SE PURGAN LOS PAQUETES -dev AL TERMINAR, y no es descuido.
#
# El primer intento hacia apt-get purge --auto-remove de las cabeceras, que
# es el idioma habitual para adelgazar la imagen. El resultado: gd, xsl y zip
# COMPILARON BIEN y luego NO CARGABAN, porque --auto-remove se llevo tambien
# las bibliotecas de EJECUCION: libpng16.so.16, libexslt.so.0, libzip.so.4.
# apt no sabe que una extension de PHP depende de ellas, porque el enlace es
# en tiempo de ejecucion y no una dependencia declarada.
#
# El fallo NO aparece al construir. Aparece al arrancar, en el registro, como
# «Unable to load dynamic library», y la aplicacion sigue en pie SIN gd -los
# timbres del SII-, SIN zip -las salidas a Excel- y SIN xsl.
#
# Cuestan unos 80 MB de imagen. Se quedan.
RUN set -eux; \
    echo 'deb http://archive.debian.org/debian bullseye main' > /etc/apt/sources.list; \
    echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99archivo; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libfreetype6-dev libjpeg62-turbo-dev libpng-dev \
        libicu-dev libxml2-dev libxslt1-dev libzip-dev \
        libssl-dev \
        ; \
    docker-php-ext-configure gd --with-freetype --with-jpeg; \
    docker-php-ext-install -j"$(nproc)" \
        gd intl mysqli pdo_mysql xsl zip exif sockets calendar \
        pcntl shmop sysvmsg sysvsem sysvshm ftp gettext opcache \
        ; \
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
