# maryun-infra

> **Si llegas nuevo a este servidor, empieza por [SERVIDOR.md](SERVIDOR.md).**
> Es el documento único: hardware, particiones, redes, puertos, qué es público
> y qué no, dónde va cada cosa, cómo conectarse a cada servicio, las reglas
> sobre secretos y exposición, y la receta para montar una aplicación nueva.

Infraestructura del servidor **maryun01** (OVH, `148.113.168.13`).

Este repositorio describe **cómo se levanta el servidor**, no con qué llaves.
Ningún secreto entra acá: viven en `/srv/secrets/` en el propio servidor, en
modo `0640 root:maryun`, y nunca se versionan.

## Por qué existe

Durante la migración desde el VPS anterior nos encontramos con que los 67
pipelines de Mage existían **solo en ese disco**, sin copia en ningún
repositorio. Este repositorio evita repetir ese error con la infraestructura:
si mañana se pierde el servidor, reconstruirlo debería ser clonar esto y cargar
los secretos, no reconstruir de memoria.

## Estructura

| Carpeta | Contenido |
|---|---|
| `stacks/` | Los `docker-compose.yml` de cada servicio |
| `bin/` | Scripts de operación (firewall, ayudantes de ClickHouse y Coolify) |
| `backup/` | Sistema de respaldos |
| `docs/` | Cómo se levanta el servidor desde cero |

## Convención del servidor

    /var/lib/docker   desechable   imágenes, capas, contenedores
    /srv              precioso     configuración, datos, secretos

Un `docker system prune -a --volumes` no puede tocar nada de `/srv`.

Los stacks viven en `/srv/stacks/<servicio>/`, con su compose, su
configuración y su `data/` montado por bind. **Ningún compose lleva
credenciales**: todas se referencian con `${VARIABLE}` desde
`/srv/secrets/<servicio>.env`, enlazado como `.env` del stack.

## Servicios

| Servicio | Publicado en | Notas |
|---|---|---|
| ClickHouse | `10.8.0.1:8123` / `:9000` | DWH. Solo VPN |
| Mage | `10.8.0.1:6789` | ETL. Solo VPN |
| Túnel MySis | `10.8.0.1:3307` | autossh hacia la VM de Azure |
| Metabase | `10.8.0.1:3000` | Solo VPN |
| Superset | `10.8.0.1:8088` | Solo VPN |
| MCP Mage / ClickHouse / Superset | `10.8.0.1:3333` / `:3334` / `:5008` | Solo VPN |
| Postgres del ERP | `10.8.0.1:5433` | Fuera de Coolify a propósito |
| ERP producción y preview | gestionados por Coolify | Único servicio público |
| Coolify | `10.8.0.1:8000` | Solo VPN |

## Red

Un solo puerto abierto a internet: **2222** (SSH).
Los puertos 80 y 443 solo aceptan tráfico desde los rangos de Cloudflare.
Todo lo demás se alcanza por **WireGuard** (`10.8.0.0/24`).

`bin/firewall-sync.sh` mantiene ambas cosas: refresca los rangos de Cloudflare
y reconstruye la cadena `DOCKER-USER`, sin la cual **Docker se salta UFW** y
cualquier puerto publicado por un contenedor queda expuesto.
