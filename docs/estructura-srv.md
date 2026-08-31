# Estructura de /srv en maryun01

Convencion unica para todo el stack. Regla de oro:

    /var/lib/docker  = desechable  (imagenes, capas, contenedores)
    /srv             = precioso    (configuracion, datos, secretos, respaldos)

Un `docker system prune -a --volumes` NO puede tocar nada de /srv.

## Directorios

| Ruta | Contenido | Permisos |
|---|---|---|
| `/srv/stacks/<servicio>/` | docker-compose.yml, Dockerfile, configuracion | 2775 root:maryun |
| `/srv/stacks/<servicio>/data/` | datos persistentes (bind mount) | segun UID del contenedor |
| `/srv/secrets/` | archivos .env centralizados | 0750 root:maryun, archivos 0640 |
| `/srv/backups/` | respaldos locales antes de salir fuera del servidor | 2775 root:maryun |
| `/srv/bin/` | scripts de operacion | 2775 root:maryun |

## Reglas

1. **Ningun .env vive junto a su stack.** Todos en `/srv/secrets/<servicio>.env`, modo 0640,
   referenciados desde compose con `env_file`. En el VPS anterior cinco .env quedaron en 644
   y un proceso comprometido (www-data) pudo leerlos. No se repite.
2. **Datos persistentes por bind mount bajo /srv**, no en volumenes con nombre. Asi un accidente
   con Docker no puede borrar datos, y el respaldo es rsync/restic directo.
3. **El ERP lo gestiona Coolify** con sus propias convenciones (/data/coolify). No se pelea con el.
4. **El grupo `maryun`** agrupa a los administradores. setgid (2775) hace que los archivos nuevos
   hereden el grupo automaticamente.
5. `/srv/backups` NO es un respaldo real: vive en el mismo RAID. Es la escala previa
   antes de empujar fuera del servidor.

## UIDs de contenedores conocidos

| Servicio | UID:GID |
|---|---|
| ClickHouse | 101:101 |
| PostgreSQL | 999:999 |
