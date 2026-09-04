# maryun01 — el servidor

Documento único de referencia. Estado verificado contra la máquina el **3 de
septiembre de 2026**.

Si vas a montar algo aquí, lee al menos: **Cómo conectarse**, **Secretos**,
**Dónde va cada cosa** y **Receta: montar una aplicación nueva**. Lo demás es
consulta.

> **Ya no es un VPS.** Es un servidor dedicado de OVH. La migración desde el VPS
> anterior (`51.222.28.249`) terminó el 1 de septiembre de 2026. Ese VPS estuvo
> comprometido: si encuentras una referencia a él en cualquier sitio, está
> obsoleta.

---

## 1 · Identidad y cómo conectarse

| | |
|---|---|
| Nombre | `maryun01` |
| IP pública | `148.113.168.13` |
| IP en la VPN | `10.8.0.1` |
| Proveedor | OVH, servidor dedicado |
| Sistema | Ubuntu 26.04 LTS, kernel 7.0.0-30-generic |
| Zona horaria | **UTC** (ojo: el negocio es Chile, UTC−4/−3) |

```bash
ssh -p 2222 ialrringo@148.113.168.13
```

**Puerto 2222, no el 22.** Autenticación **sólo por clave** — la contraseña está
desactivada y `root` no puede entrar por SSH. Los únicos usuarios permitidos son
`ialrringo` (el administrador) y `coolify` (el que usa Coolify para desplegar).

`sudo` sin contraseña para `ialrringo`. Casi todo lo operativo necesita `sudo`
porque los contenedores y `/srv/secrets` son de `root`.

### La VPN

WireGuard en `wg0`, puerto `51820/udp`. El servidor es `10.8.0.1`; los clientes
salen del rango `10.8.0.0/24`.

Media docena de servicios **sólo existen dentro de la VPN**. No es una capa
opcional: sus nombres DNS resuelven a `10.8.0.1`, que no es enrutable desde
internet. Sin VPN no se llega, y no por una regla que alguien pueda desactivar
sin querer, sino porque el nombre no apunta a ninguna parte alcanzable.

---

## 2 · Hardware y almacenamiento

| | |
|---|---|
| Placa | ASRock Rack B550D4U-2T |
| CPU | AMD Ryzen 9 5900X, 12 núcleos / 24 hilos |
| RAM | 62 GiB (≈48 GiB disponibles en operación normal) |
| Swap | 31 GiB |
| Discos | 2 × Samsung MZQL21T9HCJR de 1,7 TB, **NVMe** |
| BIOS | L0.37H (30-01-2026) |

### Cómo está particionado

Los dos NVMe están en **RAID 1 por software** (`mdadm`), o sea espejo: cada byte
se escribe en los dos discos y se sobrevive a la muerte de uno.

```
nvme0n1 + nvme1n1
   ├── md1  →  /boot/efi   511 MB   vfat
   ├── md2  →  /boot       2,0 GB   ext4
   └── md3  →  LVM, grupo de volúmenes «vg» (1,73 TB)
                  ├── lv root    150 GB  →  /
                  ├── lv docker  300 GB  →  /var/lib/docker
                  ├── lv srv     500 GB  →  /srv
                  └── SIN ASIGNAR ≈ 820 GB
```

Ocupación actual:

| punto de montaje | tamaño | usado | |
|---|---|---|---|
| `/` | 147 GB | 3,9 GB | 3 % |
| `/var/lib/docker` | 295 GB | 29 GB | 11 % |
| `/srv` | 492 GB | 13 GB | 3 % |
| `/boot` | 2,0 GB | 267 MB | 15 % |

**Están separados a propósito.** Un `docker system prune -a --volumes` puede
vaciar `/var/lib/docker` entero sin tocar un byte de `/srv`. Y un contenedor que
se descontrola llenando su log no puede llenar la raíz.

### Los ~820 GB libres

**No están perdidos: están sin asignar en el grupo de volúmenes**, que es
justamente donde conviene tenerlos. LVM permite crecer un volumen en caliente,
pero **no encogerlo sin desmontar**. Repartirlos hoy sería apostar a ciegas.

Para ampliar, cuando haga falta:

```bash
sudo lvextend -L +100G /dev/vg/srv        # o /dev/vg/docker
sudo resize2fs /dev/mapper/vg-srv         # ext4 crece en caliente
```

Usos razonables, por si sirve de guía:

- **`/srv`** — es el candidato natural. Ahí van los datos de cualquier servicio
  nuevo. ClickHouse y Postgres crecen con el histórico.
- **`/var/lib/docker`** — sólo si aparecen imágenes muy grandes. Hoy sobra: 11 %,
  y de eso una parte es caché de compilación que se poda con
  `docker builder prune`.
- **Un volumen nuevo para una base grande** — si mañana entra un motor pesado,
  darle su propio LV lo aísla del resto. Es lo que se hizo con `docker` y `srv`.
- **Dejar margen.** No hace falta asignarlo todo. 820 GB sin comprometer es una
  posición cómoda, no un desperdicio.

---

## 3 · Red

### Redes de Docker

El demonio reparte desde `172.20.0.0/14` en bloques `/24`
(`/etc/docker/daemon.json`). Las redes que importan:

| red | subred | para qué |
|---|---|---|
| `coolify` | `172.20.0.0/24` | lo que gestiona Coolify: el ERP, su base, el proxy |
| **`data`** | `172.20.1.0/24` | **todos los servicios propios**: BI, ETL, bases, MCP |
| `monitoreo-socket` | `172.20.3.0/24` | acceso acotado al socket de Docker |
| `bridge` | `172.17.0.0/16` | la de por defecto; sólo el centinela de Coolify |

**Una aplicación nueva va en `data`**, salvo que la despliegue Coolify. Es una
red externa; se declara así en el compose:

```yaml
networks:
  data:
    name: data
    external: true
```

Dentro de `data` los contenedores se resuelven **por nombre**: `clickhouse`,
`metabase-db`, `mysis_tunnel`. No uses IPs, que cambian al recrear.

### Puertos publicados

**Casi nada escucha en la IP pública.** Los servicios propios publican en
`10.8.0.1`, la dirección de la VPN:

| servicio | puerto en la VPN |
|---|---|
| Metabase | `10.8.0.1:3000` |
| Mage | `10.8.0.1:6789` |
| Superset | `10.8.0.1:8088` |
| ClickHouse | `10.8.0.1:8123` (HTTP), `:9000` (nativo) |
| Postgres del ERP | `10.8.0.1:5433` |
| Postgres espejo | `10.8.0.1:5434` |
| Túnel a MySis | `10.8.0.1:3307` |
| Uptime Kuma | `10.8.0.1:3001` |
| Beszel | `10.8.0.1:8090` |
| MCP de ClickHouse | `10.8.0.1:3334` |
| MCP de Mage | `10.8.0.1:3333` |
| MCP de Superset | `10.8.0.1:5008` |

En `0.0.0.0` sólo hay: `2222` (SSH), `80` y `443` (Traefik) y, escuchando pero
**bloqueados por el firewall**, `6001`, `6002`, `8000` y `8080` de Coolify.

### Firewall

`ufw`, activo. Lo esencial:

```
2222/tcp        desde cualquier sitio      SSH
80,443/tcp      SÓLO desde rangos de Cloudflare
51820/udp       desde cualquier sitio      WireGuard
todo en wg0     permitido                  la VPN es confiable
```

**80 y 443 no aceptan tráfico directo**: sólo llegan peticiones que hayan pasado
por Cloudflare. Los rangos los sincroniza `/srv/bin/firewall-sync.sh`. Si alguien
apunta un dominio a la IP sin pasar por Cloudflare, no funciona, y eso es
deliberado.

---

## 4 · Enrutamiento y nombres

El proxy es **Traefik**, dentro del contenedor `coolify-proxy`. Lo gestiona
Coolify, pero la configuración de los servicios propios vive en un archivo
aparte que Coolify no pisa:

```
/data/coolify/proxy/dynamic/vpn-hosts.yaml
```

### Middlewares

| nombre | qué hace |
|---|---|
| `solo-vpn` | `ipAllowList` sobre `10.8.0.0/24` |
| `freno` | límite de 20 peticiones/s, ráfaga 50, por IP real (`depth: 1`) |
| `a-https` | redirige HTTP a HTTPS |

### Quién es público y quién no

| nombre | acceso | apunta a |
|---|---|---|
| `erp.maryun.cl` | **público** | ERP, producción (Coolify) |
| `preview.maryun.cl` | **público** | ERP, preview (Coolify) |
| `metabase.maryun.cl` | **público** | Metabase |
| `reportes.maryun.cl` | **público** | Metabase (alias) |
| `superset.maryun.cl` | **público** | Superset |
| `despliegue.maryun.cl` | **público, sólo `/webhooks/`** | Coolify |
| `coolify.maryun.cl` | sólo VPN | interfaz de Coolify |
| `mage.maryun.cl` | sólo VPN | Mage |
| `monitoreo.maryun.cl` | sólo VPN | Uptime Kuma |
| `metricas.maryun.cl` | sólo VPN | Beszel |

Los públicos van con **proxy de Cloudflare activado** (nube naranja) y resuelven
a `148.113.168.13`. Los privados resuelven a `10.8.0.1` **sin** proxy.

**Por qué `despliegue.maryun.cl` existe aparte:** GitHub tiene que poder entregar
sus webhooks, y `coolify.maryun.cl` resuelve a una dirección privada. Se creó un
nombre público que **sólo acepta `PathPrefix(/webhooks/)`**; cualquier otra ruta
en ese nombre devuelve 404. La interfaz de Coolify sigue sin estar en internet.

**Cuidado si se te ocurre abrir `coolify.maryun.cl`:** con el proxy de Cloudflare
delante, Traefik ve la IP de Cloudflare y no la del cliente, así que el
`ipAllowList` sobre la VPN bloquearía a todo el mundo, incluido a ti.

### Certificados

Los emite Traefik por **DNS-01 contra Cloudflare** (resolvedor `cfdns`), así que
funcionan también para los nombres que sólo viven en la VPN. Se renuevan solos.
Hoy quedan ~63 días. El workflow de vigilancia avisa por debajo de 14.

---

## 5 · Dónde va cada cosa

La regla de oro está en `/srv/README.md` y se resume en una línea:

```
/var/lib/docker  = desechable   (imágenes, capas, contenedores)
/srv             = precioso     (configuración, datos, secretos, respaldos)
```

Un `docker system prune -a --volumes` no puede tocar nada de `/srv`.

| ruta | contenido | permisos |
|---|---|---|
| `/srv/stacks/<servicio>/` | `docker-compose.yml`, `Dockerfile`, configuración | `2775 root:maryun` |
| `/srv/stacks/<servicio>/data/` | datos persistentes por bind mount | según el UID del contenedor |
| `/srv/secrets/` | los `.env` centralizados | `0750 root:maryun`, archivos `0640` |
| `/srv/backups/` | respaldos locales antes de salir del servidor | `2775 root:maryun` |
| `/srv/bin/` | scripts de operación | `2775 root:maryun` |
| `/srv/infra/` | **repositorio git** de la infraestructura | |
| `/srv/archivo/` | material que ya no corre pero conviene conservar | |
| `/data/coolify/` | territorio de Coolify: no se pelea con él | |

**Datos por bind mount bajo `/srv`, nunca en volúmenes con nombre.** Así un
accidente con Docker no borra datos y el respaldo es una copia directa.

El grupo **`maryun`** agrupa a los administradores. El bit setgid (`2775`) hace
que lo que se cree ahí herede el grupo solo.

UIDs conocidos, por si hay que ajustar permisos de un bind mount: ClickHouse
`101:101`, PostgreSQL `999:999`.

---

## 6 · Qué corre hoy

27 contenedores. Por stack:

| stack | contenedores | qué es |
|---|---|---|
| `clickhouse` | `clickhouse`, `clickhouse-mcp` | el motor analítico; aquí viven los datos de los tableros |
| `metabase` | `metabase`, `metabase-db` | BI principal, **público** |
| `superset` | `superset`, `-db`, `-redis`, `-worker`, `-beat`, `-mcp` | BI secundario, **público** |
| `mage` | `mage`, `mage-db`, `mcp-mage` | los ETL |
| `dwh-postgres` | `dwh-postgres` | Postgres espejo para tableros |
| `mysis-tunnel` | `mysis_tunnel` | túnel SSH a MySis, el ERP viejo |
| `monitoreo` | `uptime-kuma`, `beszel`, `monitoreo-socket-proxy` | vigilancia |
| `maryun-erp` | `maryun-erp-db` | Postgres del ERP nuevo |
| (Coolify) | `coolify`, `-db`, `-redis`, `-proxy`, `-realtime`, `-sentinel` | plataforma de despliegue |
| (Coolify) | dos contenedores con nombre de UUID | el ERP: producción y preview |

`/srv/stacks/n8n` existe **vacío**: se reservó el nombre y no se montó nada.

### Los datos, dónde están

| dato | dónde |
|---|---|
| ClickHouse (`dwh`) | `/srv/stacks/clickhouse/data` |
| Postgres del ERP (`maryun_erp`, `maryun_erp_preview`) | `/srv/stacks/maryun-erp/db` |
| Postgres espejo (`dwh_espejo`) | `/srv/stacks/dwh-postgres/db` |
| Metabase (su propia base) | `/srv/stacks/metabase/db` |
| Superset (su metastore) | `/srv/stacks/superset/db` |
| Proyectos de Mage | `/srv/stacks/mage/project` (**es un repo git**) |

### Cómo conectarse a cada servicio

**Todo lo de esta tabla requiere estar en la VPN**, salvo lo marcado como
público. Las credenciales están siempre en `/srv/secrets/<servicio>.env`; léelas
desde ahí, no las copies a ningún sitio.

| servicio | desde la VPN | desde dentro de la red `data` | credenciales |
|---|---|---|---|
| Metabase | `https://metabase.maryun.cl` (**público**) | `metabase:3000` | `metabase.env` |
| Superset | `https://superset.maryun.cl` (**público**) | `superset:8088` | `superset.env` |
| Mage | `https://mage.maryun.cl` o `10.8.0.1:6789` | `mage:6789` | `mage.env` |
| Coolify | `https://coolify.maryun.cl` | `coolify:8080` | `coolify-api.env` |
| Uptime Kuma | `https://monitoreo.maryun.cl` | `uptime-kuma:3001` | `monitoreo.env` |
| Beszel | `https://metricas.maryun.cl` | — | `monitoreo.env` |

**Bases de datos:**

| motor | desde la VPN | desde la red `data` | usuarios | credenciales |
|---|---|---|---|---|
| ClickHouse | `10.8.0.1:8123` (HTTP), `:9000` (nativo) | `clickhouse:8123` / `:9000` | `admin`, `bi`, `mage`, `mcp`, `default` | `clickhouse.env` |
| Postgres del ERP | `10.8.0.1:5433` | `maryun-erp-db:5432` | `maryun`, `dwh_lector` | `maryun-erp-db.env`, `dwh-lector.env` |
| Postgres espejo | `10.8.0.1:5434` | `dwh-postgres:5432` | `dwh`, `bi_lector` | `dwh-postgres.env` |
| MySis (el ERP viejo) | `10.8.0.1:3307` | `mysis_tunnel:3306` | `appread` (**sólo lectura**) | en `io_config.yaml` de Mage |

ClickHouse tiene **un usuario por función**, no uno para todo: `bi` es el que
usan Metabase y Superset, `mage` el de los ETL, `mcp` el de las herramientas, y
`admin` sólo para administrar. Lo mismo en el Postgres espejo: `bi_lector` sólo
lee el esquema `mysis` y sólo puede crear tablas en `manual`. **Respeta esa
separación**: si montas algo que sólo consulta, dale el usuario de lectura.

Formas rápidas desde el servidor, sin exponer contraseñas:

```bash
sudo docker exec -i clickhouse clickhouse-client --multiquery <<'SQL'
SELECT count() FROM dwh.ventas_mysis;
SQL

sudo sh -c '. /srv/secrets/dwh-postgres.env && \
  docker exec -i -e PGPASSWORD="$PG_DWH_PASS" dwh-postgres \
  psql -U dwh -d dwh_espejo -X -f -' <<'SQL'
SELECT count(*) FROM mysis.ventas_mysis;
SQL
```

**`docker exec` necesita `-i`** si le pasas algo por la entrada estándar. Sin
`-i` el comando recibe un flujo vacío y no falla: simplemente no hace nada.

### Los servidores MCP

Hay cuatro, todos publicados **sólo en la VPN**:

| MCP | dirección | para qué |
|---|---|---|
| ClickHouse | `10.8.0.1:3334` | consultar el DWH |
| Mage | `10.8.0.1:3333` | ver y lanzar pipelines |
| Superset | `10.8.0.1:5008` | tableros y conjuntos de datos |
| Metabase | `10.8.0.1:3000/api/mcp` | tableros y preguntas |

El de Metabase se autentica con una **clave de API con permisos de
administrador** en la cabecera `x-api-key`. Trátala como cualquier otro secreto:
puede ejecutar SQL contra las bases conectadas.

Si un MCP no conecta, lo primero que hay que mirar es si la VPN está levantada,
no si el servicio está caído.

### MySis, el ERP viejo

Sigue vivo y es **la fuente** de los datos de ventas. Se llega por un túnel SSH
que mantiene el contenedor `mysis_tunnel` contra `20.153.168.52`.

**El usuario `appread` es de sólo lectura, y así debe quedarse.** MySis es un
sistema en producción del que depende la operación diaria: no se escribe en él
desde aquí bajo ninguna circunstancia. Si necesitas comparar contra MySis, la
forma cómoda es montarlo en ClickHouse como base de sólo lectura y desmontarlo al
terminar:

```bash
sudo /srv/bin/comparar-mysis.py --limpiar
```

---

## 7 · Secretos y exposición — esto no se negocia

**Ningún `.env` vive junto a su stack.** Todos en `/srv/secrets/<servicio>.env`,
modo `0640 root:maryun`, referenciados desde el compose con `env_file`.

No es teórico: en el VPS anterior cinco `.env` quedaron en modo 644 y un proceso
comprometido pudo leerlos. Por eso el directorio es `0750` y de `root`.

### Reglas duras

1. **Nunca imprimas el valor de un secreto.** Ni en un `echo`, ni en un mensaje
   de error, ni en un log. Ya pasó dos veces en este servidor: una clave `age` y
   una clave de API de Metabase acabaron en pantalla y hubo que rotarlas.
2. **Nunca pases un secreto como argumento de un comando.** Aparece en `ps` y en
   el historial. Pásalo por la entrada estándar o por una variable de entorno.
3. **Nunca comitees un secreto.** El repositorio `/srv/infra` describe cómo se
   levanta el servidor, **no con qué llaves**.
4. **Cuidado con las librerías que imprimen lo que reciben.** `urllib` de Python
   vuelca el valor de una cabecera cuando la rechaza. Un `\r` de más en una clave
   basta para que la escupa entera.
5. **Si un secreto se expone, se rota.** No se evalúa el riesgo, se rota.

### Qué hay (sólo los nombres)

`clickhouse.env`, `metabase.env`, `superset.env`, `mage.env`,
`maryun-erp-db.env`, `dwh-postgres.env`, `dwh-lector.env`, `monitoreo.env`,
`cloudflare-dns.env`, `ovh-backup.env`, `r2-respaldo.env`, `coolify-api.env`,
`mcp-mage.env`, las claves de despliegue (`*-deploy-key`), `mysis-ssh`,
`respaldo-age.key` / `.pub`, `boveda.yaml.age` y **`RECUPERACION.txt`**.

`RECUPERACION.txt` es el documento con lo necesario para recuperar los respaldos
cifrados. Si se pierde, los respaldos externos son ilegibles.

### Cómo se usan en la práctica

**Un compose nunca lleva credenciales dentro.** Las declara y las recibe:

```yaml
services:
  miapp:
    env_file:
      - /srv/secrets/miapp.env      # el contenedor recibe TODO el archivo
    environment:
      DB_HOST: postgres             # esto no es secreto, va aquí
      DB_PASSWORD: ${MIAPP_PASS}    # esto se sustituye al levantar
```

La sustitución de `${...}` la hace `docker compose` al leer el compose, y para eso
hay que decirle de dónde:

```bash
sudo docker compose --env-file /srv/secrets/miapp.env up -d
```

Son dos mecanismos distintos y conviene no confundirlos: `env_file` inyecta
variables **dentro del contenedor**; `--env-file` alimenta la **sustitución en el
propio compose**. Muchos stacks de aquí usan los dos.

**Crear uno:**

```bash
sudo touch /srv/secrets/miapp.env
sudo chown root:maryun /srv/secrets/miapp.env
sudo chmod 640 /srv/secrets/miapp.env
sudo nano /srv/secrets/miapp.env
```

**Leer un valor sin exponerlo.** Cárgalo en un subshell y úsalo ahí; nunca lo
imprimas ni lo pases como argumento:

```bash
sudo sh -c '. /srv/secrets/dwh-postgres.env && \
  docker exec -i -e PGPASSWORD="$PG_DWH_PASS" dwh-postgres psql -U dwh -d dwh_espejo -X -f -' <<'SQL'
SELECT 1;
SQL
```

Para generar uno nuevo: `head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32`.

**Rotar** es siempre el mismo baile: crear la credencial nueva en el servicio,
comprobar que funciona, **después** borrar la vieja, y sólo entonces actualizar el
`.env` y recrear el contenedor. En ese orden: si borras primero y la nueva falla,
te quedas fuera.

### La bóveda cifrada

Además de los `.env` en claro —protegidos por permisos— hay una **bóveda cifrada
en reposo**: `/srv/secrets/boveda.yaml.age`, cifrada con `age` contra
`respaldo-age.pub`.

```bash
sudo /srv/bin/secretos.sh ver          # lista los NOMBRES de las claves, nunca los valores
sudo /srv/bin/secretos.sh leer NOMBRE  # imprime un valor concreto
sudo /srv/bin/secretos.sh editar       # abre el editor con el contenido descifrado
```

Al guardar, se vuelve a cifrar solo. El archivo temporal vive en `/dev/shm`, que
es memoria: **el texto en claro no toca el disco en ningún momento**.

Es el mecanismo de SOPS hecho a mano, y está así a propósito, como paso previo a
decidir si se adopta SOPS de verdad. Lo que SOPS añadiría, por si se plantea:
cifra **valor por valor** en vez del archivo entero —así el diff de git muestra
qué clave cambió sin revelar ninguna—, admite varias llaves a la vez, y trae
`sops exec-env`, que inyecta las variables a un proceso sin escribirlas nunca a
disco.

### No expongas nada que no haga falta

**Por omisión, un servicio nuevo va sólo a la VPN.** Publicarlo en internet es
una decisión aparte, que se toma cuando alguien lo necesita de verdad y con
nombre y apellido, no «por si acaso».

Publicar no es cambiar un DNS. Cuando algo pasa a ser público:

- **su pantalla de acceso queda a la vista de cualquiera**, y con ella su
  versión, su tecnología y su formulario de login;
- **lo que la propia aplicación considere público pasa a serlo de verdad.**
  Cuando Metabase se hizo público, un tablero con enlace compartido —datos de
  cobranzas— quedó **legible sin sesión desde internet**. El DNS no lo hizo
  visible: lo hizo alcanzable, que es distinto y peor;
- **las contraseñas débiles dejan de ser un detalle interno.** El mínimo de
  Metabase eran 6 caracteres y un dígito;
- **las cuentas de administrador de más se vuelven un problema.** Había siete
  entre las dos herramientas de BI, una de ellas sin usar desde hacía nueve
  meses.

La lista corta antes de exponer cualquier cosa:

1. ¿Hace falta de verdad, o basta con la VPN?
2. ¿Qué considera «público» la aplicación por su cuenta? Enlaces compartidos,
   embebidos, endpoints sin autenticación, APIs.
3. ¿Cuánto pide de contraseña? ¿Quién es administrador y por qué?
4. ¿Lleva el middleware `freno`? Todo lo público debe llevarlo.
5. ¿Va detrás del proxy de Cloudflare? El firewall sólo acepta 80 y 443 desde
   sus rangos, así que sin proxy no funciona — y eso es una protección, no un
   estorbo.
6. ¿Se puede acotar por ruta? `despliegue.maryun.cl` es público **sólo** en
   `/webhooks/`; cualquier otra ruta devuelve 404.

Y al revés: **nada de lo que hoy está sólo en la VPN necesita salir.** Coolify,
Mage, Uptime Kuma, Beszel, todas las bases y los cuatro MCP están bien donde
están. Si te encuentras a punto de publicar uno, para y pregunta.

**Los puertos, igual.** Un servicio nuevo publica en `10.8.0.1:<puerto>`, nunca
en `0.0.0.0`. Un `ports: - "9999:8080"` sin dirección abre el puerto en **todas**
las interfaces.

En una máquina normal eso además **se saltaría el firewall**, porque Docker
escribe sus reglas por delante de las de `ufw`. Aquí no, y conviene saber por
qué: `/srv/bin/firewall-sync.sh` reconstruye la cadena `DOCKER-USER`, que es el
gancho oficial para filtrar el tráfico hacia puertos publicados por
contenedores. Sólo pasan las conexiones ya establecidas, las redes privadas y
los rangos de Cloudflare hacia 80 y 443.

O sea que hay dos capas, y **las dos importan**: publicar en `10.8.0.1` es la
primera, y `DOCKER-USER` es la red de seguridad por si alguien olvida la primera.
Si tocas ese script, comprueba con `sudo iptables -L DOCKER-USER -n` que la
cadena sigue en pie: si queda vacía, cada puerto publicado pasa a estar abierto a
internet sin que nada lo anuncie.

---

## 8 · Despliegues

**El ERP lo despliega Coolify. Todo lo demás es `docker compose` a mano.**

### El ERP

| entorno | URL | rama | base |
|---|---|---|---|
| Producción | `erp.maryun.cl` | `production` | `maryun_erp` |
| Preview | `preview.maryun.cl` | `main` | `maryun_erp_preview` |

Un push a `main` despliega preview solo; publicar es fusionar `main` en
`production`. Lo dispara un **webhook de GitHub** contra
`https://despliegue.maryun.cl/webhooks/...`.

**Las migraciones las aplica el contenedor al arrancar**, desde
`docker/arrancar.sh` en el repositorio del ERP: corre `prisma migrate deploy` y
sólo después levanta la aplicación. Si la migración falla, el contenedor no
arranca y queda el anterior sirviendo.

El detalle completo está en `ENTORNOS.md`, dentro del repositorio del ERP, que se
declara la fuente única de esos hechos.

```bash
sudo /srv/bin/ver-arranque.sh    # cómo arrancó cada contenedor del ERP
sudo /srv/bin/ver-webhooks.sh    # los secretos de webhook, descifrados
```

### Todo lo demás

```bash
cd /srv/stacks/<servicio>
sudo docker compose --env-file /srv/secrets/<servicio>.env up -d
```

---

## 9 · Respaldos

Dos escalones, y el primero **no** es un respaldo:

1. **`/srv/backups`** — volcados locales, retención 14 días. Vive en el mismo
   RAID, así que no protege de perder la máquina. Es la escala previa.
2. **OVH, cifrado** — `rclone` con cifrado, sobre `age`. Retención 30 días.

Diario a las **03:15 UTC** (`maryun-respaldo.timer`). Qué entra: los volcados de
todos los Postgres, un respaldo consistente de ClickHouse (no una copia en
caliente del directorio), los adjuntos y `/srv/secrets` cifrado.

```bash
sudo /srv/bin/ver-generaciones.sh      # qué hay fuera del servidor
sudo /srv/bin/borrar-respaldo-viejo.sh # se niega si hay menos de 3 generaciones nuevas
```

**Pendiente:** queda un destino viejo, `ovh:maryun01`, cifrado con claves que se
filtraron y ya se rotaron. Se borra cuando `maryun01-v2` tenga 3 generaciones.

### PITR: la base del ERP aparte

`maryun_erp` **no** se conforma con el volcado diario. Desde el 4-sep-2026 tiene
archivado continuo de WAL con pgBackRest, así que se puede volver a **cualquier
instante**, no sólo al último respaldo.

Por qué sólo esta base: el ERP emite unos 3.100 documentos al SII cada día y un
DTE aceptado no se puede des-enviar. Restaurar al volcado de ayer deja la base
sin facturas que el SII sí tiene, con folios quemados que la base cree libres —
y eso se reconcilia a mano, folio por folio. La ventana de pérdida pasa de
24 horas a **un minuto**.

```
  maryun-erp-db ──(archive_command)──► /srv/pitr ──(rclone crypt, 15 min)──► R2
```

El repositorio local es el único que toca `archive_command`, a propósito: si el
archivado dependiera de la red, un corte de red haría crecer `pg_wal` hasta
llenar el disco y **PostgreSQL se detendría**. Es el modo de fallo clásico del
PITR y aquí no puede ocurrir por un problema de red.

```bash
sudo /srv/bin/pitr.sh info      # hasta dónde se puede volver
sudo /srv/bin/pitr.sh check     # ¿el archivado funciona de verdad?
sudo /srv/bin/pitr.sh ensayo    # restaurar de verdad, sin tocar producción
```

Ocupa 132 MB tras la primera copia completa, y se estabiliza en el orden de 5 a
10 GB con la retención puesta: **30 días**, tanto en el servidor como en R2.

**El runbook de recuperación, las trampas y la verificación están en
[`docs/pitr.md`](docs/pitr.md).** ClickHouse no tiene equivalente: su ventana
sigue siendo el respaldo diario.

---

## 10 · Vigilancia y tareas programadas

| temporizador | cuándo | qué hace |
|---|---|---|
| `maryun-respaldo` | 03:15 UTC | respaldo diario |
| `maryun-archivos` | cada hora | copia los adjuntos del ERP a R2 |
| `maryun-pitr-full` | domingos 04:00 UTC | copia base completa de `maryun_erp` |
| `maryun-pitr-diff` | 04:30 UTC | copia base diferencial de `maryun_erp` |
| `maryun-pitr-externo` | cada 15 min | empuja el repositorio de PITR a R2, cifrado |
| `maryun-pitr-vigilar` | cada 10 min | vigila el archivado de WAL, `pg_wal` y el espacio |
| `maryun-pitr-ensayo` | 1.er domingo de mes | **restaura de verdad** y avisa si falla |
| `maryun-espejo-postgres` | 07:30 UTC | refresca el Postgres espejo |
| `maryun-preview` | cada hora, min. 40 | copia producción a preview (base y bucket) y reinicia la aplicación. Se salta si existe `/srv/PREVIEW-CONGELADO`. Detalle en [`docs/preview.md`](docs/preview.md) |
| `maryun-discos` | diario | lee el SMART de los NVMe |
| `maryun-red` | cada minuto | registra caídas de red con su duración |
| `maryun-arranque` | al arrancar | anota si la caída fue en seco y cuánto duró |

Dentro del servidor: **Uptime Kuma** (`monitoreo.maryun.cl`) y **Beszel**
(`metricas.maryun.cl`), los dos sólo por VPN.

**Los tres monitores corren dentro de la máquina que vigilan.** Si se cae el
servidor, no avisa ninguno — ya pasó dos veces. Por eso hay además un workflow de
GitHub Actions en el repositorio `maryun-infra` que comprueba desde fuera cada 30
minutos. Detalle en `docs/monitoreo-externo.md`.

---

## 11 · Receta: montar una aplicación nueva

Los pasos, en orden, siguiendo las convenciones de la casa.

**1. Decide si la despliega Coolify o va a mano.** Coolify, sólo si es una
aplicación con repositorio git que quieras desplegar por push. Cualquier otra
cosa —una base, una herramienta, un servicio de terceros— va a mano en
`/srv/stacks`.

**2. Crea el stack.**

```bash
sudo mkdir -p /srv/stacks/miapp/data
sudo chown -R root:maryun /srv/stacks/miapp
sudo chmod 2775 /srv/stacks/miapp
```

**3. El secreto, aparte.**

```bash
sudo touch /srv/secrets/miapp.env
sudo chown root:maryun /srv/secrets/miapp.env
sudo chmod 640 /srv/secrets/miapp.env
```

**4. El compose.** `/srv/stacks/miapp/docker-compose.yml`:

```yaml
name: miapp

services:
  miapp:
    image: loquesea:version-exacta      # fija la versión, nunca «latest»
    container_name: miapp
    restart: unless-stopped

    # Sólo por la VPN. Elige un puerto libre; mira la tabla del punto 3.
    ports:
      - "10.8.0.1:9999:8080"

    env_file:
      - /srv/secrets/miapp.env

    volumes:
      - /srv/stacks/miapp/data:/var/lib/loquesea

    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8080/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s

    deploy:
      resources:
        limits:
          memory: 2G                    # pon un techo siempre

    logging:
      driver: json-file
      options: { max-size: "50m", max-file: "3" }

    networks: [data]

networks:
  data:
    name: data
    external: true
```

**5. Levántalo.**

```bash
cd /srv/stacks/miapp
sudo docker compose --env-file /srv/secrets/miapp.env up -d
```

**6. Si necesita nombre DNS**, decide primero si es público o de la VPN.

*Sólo VPN* — registro `A` en Cloudflare apuntando a `10.8.0.1`, **sin proxy**, y
un router en `/data/coolify/proxy/dynamic/vpn-hosts.yaml`:

```yaml
    miapp-vpn:
      rule: "Host(`miapp.maryun.cl`)"
      entryPoints: [https]
      service: miapp-vpn
      middlewares: [solo-vpn]
      tls:
        certResolver: cfdns
        domains: [{ main: miapp.maryun.cl }]
```

más su servicio:

```yaml
    miapp-vpn:
      loadBalancer:
        servers:
          - url: "http://10.8.0.1:9999"
```

*Público* — registro `A` a `148.113.168.13` **con** proxy de Cloudflare, y el
router **sin** `solo-vpn` pero **con** `freno`. Antes de publicar algo, lee el
punto 12: publicar tiene consecuencias que no son obvias.

Traefik recarga el archivo solo; no hace falta reiniciarlo.

**7. Versiónalo.** Copia el compose al repositorio de infraestructura y comitea:

```bash
sudo cp /srv/stacks/miapp/docker-compose.yml /srv/infra/stacks/miapp/
cd /srv/infra && git add -A && git commit && git push origin main
```

**Ese `git push` hay que hacerlo como `ialrringo`, no con `sudo`.** El alias
`github-infra` y su clave están en la configuración SSH de ese usuario; con
`sudo` se usa la de `root` y falla. Si ya usaste `sudo` para editar, arregla los
propietarios antes: `sudo chown -R ialrringo:maryun /srv/infra`.

**8. Vigílalo.** Añádelo a Uptime Kuma y, si es público, a la lista del workflow
`.github/workflows/vigilancia-externa.yml` del repositorio de infraestructura.

---

## 12 · Trampas conocidas

Cosas que ya mordieron aquí. Léelas antes de repetirlas.

**El servidor está en UTC y el negocio en Chile.** Cualquier corte de día que
mires en la consola está desplazado 3 o 4 horas respecto a lo que ve un usuario.

**Publicar un servicio no es sólo DNS.** Cuando Metabase pasó a ser público, un
tablero con enlace compartido quedó **legible sin sesión desde internet**. Antes
de exponer algo, revisa lo que la propia aplicación considera público.

**Cloudflare bloquea clientes que no parecen navegador.** Una petición de script
a un dominio con proxy puede recibir un 403 con «error 1010». `curl` pasa;
`urllib` de Python no. Si automatizas contra un servicio propio, ve por la VPN o
por la IP interna, no por el nombre público.

**`docker exec` sin `-i` dentro de un heredoc no recibe nada.** Y
`docker compose exec -T` se come el heredoc de fuera. Si le pasas SQL o un script
por la entrada estándar, `-i` es obligatorio.

**Coolify regenera `/data/coolify/proxy/docker-compose.yml`.** Si lo hace, se
pierden el registro de acceso y el resolvedor `cfdns` que se añadieron a mano.
Hay copia en `/srv/infra/proxy/`.

**Las columnas `MATERIALIZED` de ClickHouse no salen en `SELECT *`.** Si recreas
una vista con `v.*`, desaparecen y todo lo que las consumía se rompe. Hay que
listarlas explícitamente. Las `DEFAULT` sí salen, y listarlas otra vez es un error.

**No hagas `git add -A` en el directorio personal de una máquina de trabajo.** En
el PC del administrador, `C:\Users\Ian` es un repositorio de git sin remoto.

**Antes de borrar filas «duplicadas», comprueba que lo sean.** En el DWH hay
claves que colisionan entre tablas distintas del origen: dos registros legítimos
pueden compartir clave. Se verifica antes, no después.

**Valida antes de escribir un archivo, no después.** Un parche que escribió
primero y validó después dejó un script roto en el disco.

---

## 13 · `maryun-infra`: el repositorio de la infraestructura

### Qué es y por qué existe

Un repositorio git que describe **cómo se levanta este servidor**. La idea es que
si mañana se pierde la máquina, reconstruirla sea *clonar esto y cargar los
secretos*, no reconstruir de memoria.

No es una precaución abstracta. Durante la migración desde el VPS anterior se
descubrió que los **67 pipelines de Mage existían sólo en ese disco**, sin copia
en ningún repositorio. Este repositorio existe para no repetirlo con la
infraestructura.

### Dónde vive

| | |
|---|---|
| En el servidor | `/srv/infra` — **es la copia de trabajo**, no un destino de despliegue |
| Remoto | `git@github-infra:IanIgNNNacio/maryun-infra.git` |
| En el PC de Ian | `C:\Users\Ian\Maryun\maryun-infra` |

`github-infra` **no es un dominio**: es un alias de SSH definido en
`~/.ssh/config` del usuario `ialrringo`, que apunta a `github.com` con la clave
`~/.ssh/infra-deploy-key`.

### Lo que hay que entender antes de tocarlo

**Es una copia, no la configuración viva.** Lo que corre es
`/srv/stacks/<servicio>/docker-compose.yml`; lo versionado es
`/srv/infra/stacks/<servicio>/docker-compose.yml`. Son dos archivos distintos y
**se sincronizan a mano**.

Consecuencia práctica: si editas un stack y no copias el cambio al repositorio,
el repositorio miente. Y si editas el repositorio creyendo que cambias el
servidor, no cambias nada. La costumbre es: **cambiar en `/srv/stacks`, probar
que funciona, y sólo entonces copiar y comitear.**

### Estructura

```
/srv/infra/
├── README.md              apunta a SERVIDOR.md
├── SERVIDOR.md            este documento
├── docs/                  documentación temática
├── bin/                   copia de los scripts de /srv/bin
├── stacks/                copia de los compose de /srv/stacks
├── proxy/                 copia de la configuración de Traefik
├── systemd/               las unidades .service y .timer
├── backup/                lo relativo a respaldos
├── migracion/             material de la migración desde el VPS
└── .github/workflows/     la vigilancia externa
```

### Cómo trabajar con él

```bash
cd /srv/infra
sudo cp /srv/stacks/miapp/docker-compose.yml stacks/miapp/
sudo chown -R ialrringo:maryun .        # ver la trampa de abajo
git add -A
git commit -m "..."
git push origin main
```

**La trampa, y muerde siempre la primera vez:** el `git push` hay que hacerlo
**como `ialrringo`, sin `sudo`**. El alias `github-infra` y su clave viven en la
configuración SSH de ese usuario; con `sudo` se usa la de `root`, que no la
tiene, y el push falla con *«Please make sure you have the correct access
rights»*.

Y peor: si usaste `sudo` para editar o copiar, los archivos —incluidos los de
`.git/`— quedan de `root`, y las operaciones siguientes fallan de formas raras.
Por eso el `chown` antes de comitear. Ya pasó: una vez quedaron 79 archivos de
`.git` con el propietario equivocado.

### Qué entra y qué no

**Entra:** compose, Dockerfile, configuración, scripts de operación, unidades de
systemd, documentación, workflows.

**No entra jamás:** nada de `/srv/secrets`. El `.gitignore` es deliberadamente
agresivo —`*.env`, `*secret*`, `*password*`, `*.key`, `*.pem`, `*deploy-key*`,
volcados, respaldos— bajo el criterio de que **es preferible que se escape un
archivo inocente a que se suba uno con credenciales**. Si algo tuyo no aparece en
`git status`, mira el `.gitignore` antes de forzarlo con `git add -f`.

Los `.env` sí pueden tener plantilla: `*.env.ejemplo` y `*.env.plantilla` están
exceptuados, y sirven para documentar **qué claves** hace falta definir sin
decir con qué valores.

### Los documentos

| documento | de qué trata |
|---|---|
| `/srv/README.md` | la convención de `/srv`, con permisos y UIDs |
| `docs/estructura-srv.md` | detalle de la estructura |
| `docs/monitoreo.md` | la vigilancia interna |
| `docs/monitoreo-externo.md` | la vigilancia desde fuera, y sus tres trampas |
| `docs/postgres-espejo.md` | el Postgres espejo, y qué gana frente a ClickHouse |
| `docs/postgres-vs-clickhouse-en-bi.md` | qué puede hacer cada motor en Metabase y Superset |
| `docs/ventas-no-calzan.md` | por qué las ventas del DWH no cuadraban, y cómo se arregló |
| `docs/fin-del-periodo-comercial.md` | el paso del ciclo 26-25 a meses calendario |
| `docs/acceso-bi-cloudflare.md` | cómo poner Cloudflare Access delante del BI |
| `docs/ticket-ovh-reinicios.md` | los dos reinicios en frío sin explicar |
| `docs/superset-notas.md` | notas de Superset |
| `docs/recomendaciones-produccion.md` | recomendaciones pendientes |
| `bin/` | los scripts de operación, todos comentados |

Y en el repositorio del ERP (`maryun-erp`): **`ENTORNOS.md`**, que es la fuente
única sobre qué rama despliega dónde, y `PENDIENTES-CONTABLES.md` con lo abierto
en materia contable.

---

## 14 · Lo que está pendiente

Para que no se descubra por sorpresa:

- **El VPS viejo sigue encendido** (`51.222.28.249`), con el 443 abierto y
  comprometido. Ningún DNS apunta ahí. Hay que apagarlo.
- **La GitHub App vieja** (`app_id 4575312`) sigue existiendo; su clave privada
  estuvo en ese VPS.
- **El proceso que consulta el estado de los DTE en el SII lleva todo 2026
  caído.** Nadie sabe qué facturas fueron aceptadas o rechazadas este año.
- **El ETL de ventas (`ventas_mysis`) no entra en el refresco nocturno**, a
  diferencia de las otras nueve tablas espejo. Por eso acumula deriva.
- **Cloudflare Access** está documentado y sin activar.
- Dos reinicios en frío sin explicación, con **ticket redactado y sin enviar**.
