# PITR de la base del ERP

Recuperación a un instante cualquiera (*point-in-time recovery*) para
`maryun_erp`. Montado el 4 de septiembre de 2026.

---

## 1 · Por qué

Antes de esto el único respaldo de la base del ERP era el volcado diario de las
03:15 UTC. La ventana de pérdida era de **hasta 24 horas**.

El problema no es el volumen de trabajo perdido, es que **el SII no olvida**. El
ERP emite unos 3.100 documentos tributarios al día y los manda al SII, que los
guarda. Un DTE aceptado no se puede des-enviar.

Si se restaura al volcado de ayer:

- La base **no conoce** las facturas emitidas hoy. El SII sí.
- Los folios que la base cree libres el SII ya los tiene usados. Reemitirlos es
  un folio duplicado y el SII lo rechaza.
- El cliente tiene su factura en la mano y el sistema no la registra, así que no
  se cobra.

Arreglar eso es bajar el registro del SII y reconstruir a mano, folio por folio.

Con PITR la ventana pasa de 24 horas a **un minuto**.

---

## 2 · Cómo está montado

```
  PostgreSQL 18.6 (maryun-erp-db)
        │  archive_command = pgbackrest --stanza=maryun archive-push %p
        │  archive_timeout = 60
        ▼
  /srv/pitr                      ← repositorio local, pgBackRest 2.59.1
    ├── archive/maryun/18-1/     ← cada segmento de WAL, comprimido con zstd
    └── backup/maryun/           ← copias base (completas y diferenciales)
        │
        │  rclone crypt, cada 15 minutos
        ▼
  R2  ·  maryun-erp-respaldo/pitr/   ← cifrado en origen
```

El bucket `maryun-erp-respaldo` lo comparten dos cosas, cada una en su prefijo:

| prefijo | qué | quién lo escribe |
|---|---|---|
| `pitr/` | la base: WAL + copias base, **cifrado** | `maryun-pitr-externo`, cada 15 min |
| `erp/` | el almacenamiento de objetos del ERP, en claro | `maryun-archivos`, cada hora |

**Los prefijos separados no son cosmética.** `archivos.sh` verifica que el
destino no tenga menos objetos que el origen; cuando contaba el bucket entero,
los objetos del PITR inflaban la cuenta y ese guardarraíl dejaba de servir —
podía faltar la mitad de los adjuntos y seguir dando OK. Corregido el
4-sep-2026, el mismo día que el PITR empezó a compartir el bucket.

Y el prefijo se llama `erp/`, no `adjuntos/`: el bucket de origen no guarda sólo
adjuntos. El ERP separa por **espacio** —`adjuntos` de facturas y nóminas,
`productos` del catálogo— y cada espacio es un prefijo dentro del bucket. Con un
prefijo llamado `adjuntos/` quedaba `adjuntos/adjuntos/facturas/…`, que además
de feo dejaba de ser verdad en cuanto apareciera otro espacio.

**Qué NO está en R2.** Sólo estas dos cosas. El respaldo diario de todo lo demás
—los volcados de los seis Postgres, ClickHouse, los pipelines de Mage, la
configuración de Coolify y `/srv/secrets` cifrado— va al **Backup Storage de
OVH**, no aquí: son unos 2 GB por generación, con 3 generaciones y retención de
30 días. Se consulta con `sudo /srv/bin/ver-generaciones.sh`.

**El repositorio local es el único que toca `archive_command`, y es
deliberado.** Si el archivado dependiera de la red, un corte de red haría que
PostgreSQL dejara de reciclar WAL; `pg_wal` crecería hasta llenar el disco y el
motor se detendría. Es el modo de fallo clásico del PITR. Aquí un problema de
red retrasa la copia de fuera, pero nunca puede tumbar la base.

La copia de fuera va cifrada con `RCLONE_CRYPT_PASSWORD`, la misma passphrase
del respaldo diario, documentada en `/srv/secrets/RECUPERACION.txt`.
**Sin esa passphrase la copia de R2 es ilegible.**

### La imagen

`maryun/postgres-pgbackrest:18.6` = `postgres:18.6` + `pgbackrest`. Hace falta
una imagen propia porque `archive_command` lo ejecuta el propio motor, dentro
del contenedor: no sirve un contenedor aparte.

Se construye desde `/srv/stacks/maryun-erp/Dockerfile`:

```bash
cd /srv/stacks/maryun-erp && sudo docker compose build && sudo docker compose up -d
```

**Al subir de versión de PostgreSQL hay que cambiarla en dos sitios**: el `FROM`
del `Dockerfile` y el `image:` del `docker-compose.yml`.

---

## 3 · Qué protege y qué no

| | ventana de pérdida |
|---|---|
| `maryun_erp` (producción) | **1 minuto** |
| `maryun_erp_preview` | 1 minuto (va en el mismo cluster) |
| ClickHouse (`dwh`, `logistica`) | 24 h — el respaldo diario. **ClickHouse no tiene PITR**, no funciona con reproducción de WAL. Tiene `BACKUP`/`RESTORE` con incrementales, que es otro mecanismo. |
| `dwh_espejo` (Postgres espejo) | irrelevante — se reconstruye desde ClickHouse en ~40 s con `/srv/bin/espejo-mysis-a-postgres.py` |
| Metabase, Superset, Mage, Coolify | 24 h — el respaldo diario |

**Por qué el espejo no lleva PITR:** genera 6.829 MB de WAL al día, once veces
más que el ERP, porque se reescribe entero cada noche. Sería gastar espacio
protegiendo algo que ya es reproducible.

### Preview

`maryun_erp_preview` vive en el mismo cluster que producción, así que el PITR y
el volcado diario la cubren igual. Lo que **ya no existe** es lo que había en el
VPS viejo: `scripts/replica/sync.sh` traía producción desde Neon cada hora y de
paso podía refrescar preview, para que las pruebas se hicieran con datos reales.
Ese script dependía de Neon y de Vercel, que ya no están, y **no está instalado
en este servidor** — no hay cron ni temporizador que lo llame. Preview quedó
congelada en la instantánea con la que se creó (210 MB frente a los 289 MB de
producción).

Tampoco se respalda su bucket: `maryun-archivos` copia sólo `maryun-erp`, no
`maryun-erp-preview`. Es defendible —preview es desechable por definición— pero
conviene que sea una decisión y no un olvido.

---

## 4 · Lo que ocupa, medido

| | |
|---|---|
| Base `maryun_erp` | 292 MB |
| Copia completa comprimida | **127,6 MB** (de 539,3 MB, con zstd) |
| Tiempo de una copia completa | **2,5 segundos** |
| WAL generado | ~612 MB/día antes de comprimir |
| Repositorio local tras el primer completo | 132 MB |
| Retención local | **30 días** (por tiempo, no por número de copias) |
| Retención en R2 | **30 días** |
| Espacio libre en `/srv` | 453 GB |

Con esta retención el repositorio se estabiliza en el orden de **5 a 10 GB**.
El espacio no es una restricción aquí.

---

## 5 · Operación

Todo pasa por `/srv/bin/pitr.sh`.

```bash
sudo /srv/bin/pitr.sh info        # qué hay y hasta dónde se puede volver
sudo /srv/bin/pitr.sh check       # ¿el archivado funciona de verdad?
sudo /srv/bin/pitr.sh full        # copia completa
sudo /srv/bin/pitr.sh diff        # copia diferencial
sudo /srv/bin/pitr.sh externo     # empujar a R2 y aplicar retención allá
sudo /srv/bin/pitr.sh ensayo      # restaurar de verdad, sin tocar producción
```

### Temporizadores

| unidad | cuándo | qué hace |
|---|---|---|
| `maryun-pitr-full` | domingos 04:00 UTC | copia completa |
| `maryun-pitr-diff` | diario 04:30 UTC | copia diferencial |
| `maryun-pitr-externo` | cada 15 min | copia el repositorio a R2 |
| `maryun-pitr-vigilar` | cada 10 min | comprueba archivado, espacio y edad de las copias |
| `maryun-pitr-ensayo` | primer domingo de mes, 05:00 UTC | **restaura de verdad** y avisa si falla |

### La vigilancia no es opcional

`/srv/bin/vigilar-pitr.sh` comprueba seis cosas y avisa por Telegram con
enfriamiento de 6 horas por tipo de aviso:

1. que el archivado no esté fallando (`pg_stat_archiver`)
2. cuánto WAL hay escrito y aún sin archivar (umbral 256 MB)
3. el tamaño de `pg_wal` (umbral 4 GB) — **el síntoma de que se atascó**
4. espacio libre en `/srv` (umbral 15 %)
5. edad del último respaldo del repositorio (umbral 36 h)
6. edad de la última copia a R2 (umbral 6 h)

Sin esto el archivado continuo es un riesgo en vez de una protección: es
exactamente el mecanismo que puede llenar el disco y detener el motor.

Para verla en directo:

```bash
sudo /srv/bin/vigilar-pitr.sh --verboso
```

---

## 6 · Runbook: restaurar

### Caso A — comprobar que se puede restaurar (sin tocar nada)

```bash
sudo /srv/bin/pitr.sh ensayo "2026-09-04 01:32:40-04"
```

Levanta un PostgreSQL nuevo en `127.0.0.1:5439` con los datos tal como estaban
en ese instante. Producción no se toca: el repositorio se monta de sólo lectura.

```bash
docker exec -it pitr-ensayo psql -U maryun -d maryun_erp
docker rm -f pitr-ensayo && sudo rm -rf /srv/pitr-ensayo   # al terminar
```

**La hora es la de `America/Santiago`.** Conviene escribir el desfase explícito
(`-04`) para que no haya ambigüedad.

### Caso B — recuperar producción a un instante

Esto sí es serio. **Antes de empezar, parar las aplicaciones** para que nadie
escriba durante la operación.

```bash
# 1. parar lo que escribe en la base
sudo docker stop <contenedores del ERP en Coolify>

# 2. restaurar en una instancia aparte y COMPROBAR que el instante es el bueno
sudo /srv/bin/pitr.sh ensayo "2026-09-04 01:32:40-04"
docker exec -it pitr-ensayo psql -U maryun -d maryun_erp   # verificar aquí

# 3. sólo cuando esté verificado: apartar el directorio actual
cd /srv/stacks/maryun-erp && sudo docker compose down
sudo mv /srv/stacks/maryun-erp/db/18/docker /srv/stacks/maryun-erp/db/18/docker.antes-de-restaurar

# 4. restaurar sobre el sitio real
sudo mkdir -p /srv/stacks/maryun-erp/db/18/docker
sudo chown 999:999 /srv/stacks/maryun-erp/db/18/docker
sudo docker run --rm -u postgres \
  -v /srv/pitr:/var/lib/pgbackrest:ro \
  -v /srv/stacks/maryun-erp/pgbackrest:/etc/pgbackrest:ro \
  -v /srv/stacks/maryun-erp/db/18/docker:/destino \
  --entrypoint pgbackrest maryun/postgres-pgbackrest:18.6 \
  --stanza=maryun --pg1-path=/destino --log-path=/tmp \
  restore --type=time --target="2026-09-04 01:32:40-04" --target-action=promote

# 5. arrancar
sudo docker compose up -d
```

**No borrar `docker.antes-de-restaurar` hasta que todo esté comprobado.**

Tras una recuperación la base arranca en una **línea de tiempo nueva**. Eso es
correcto y pgBackRest lo gestiona, pero conviene hacer un `full` en cuanto la
cosa se estabilice:

```bash
sudo /srv/bin/pitr.sh full
```

### Caso C — el servidor ya no existe

El repositorio está en R2, cifrado. Hace falta:

1. `RCLONE_CRYPT_PASSWORD` — de `/srv/secrets/RECUPERACION.txt`, cuya copia
   está **fuera del servidor**, en poder de Ian.
2. Las credenciales de R2 (`R2_RESPALDO_*`), en el mismo documento.

Se baja el repositorio con `rclone copy` invirtiendo origen y destino respecto a
lo que hace `pitr.sh externo`, se pone en `/srv/pitr` de la máquina nueva, y a
partir de ahí es el caso B.

El enlace `latest` no se copia a R2 (rclone no puede seguirlo) y **no hace
falta**: la lista de respaldos está en `backup.info`.

---

## 7 · Trampas conocidas

**`max_connections` de la instancia que restaura.** PostgreSQL se niega a
recuperar si tiene menos plazas que la instancia que generó el WAL:

```
FATAL: recovery aborted because of insufficient parameter settings
DETAIL: max_connections = 100 is a lower setting than on the primary server,
        where its value was 200.
```

Producción va con `max_connections=200`, así que cualquier instancia de
recuperación tiene que arrancar al menos con eso. Lo mismo valdría para
`max_worker_processes`, `max_locks_per_transaction` y `max_prepared_transactions`
si alguna vez se suben. `pitr.sh ensayo` ya lo hace.

**El entrypoint de la imagen oficial.** Mira `$PGDATA`
(`/var/lib/postgresql/18/docker`), lo ve vacío y exige `POSTGRES_PASSWORD`. En
una restauración los datos están en otra ruta, así que hay que saltárselo con
`--entrypoint postgres` y pasar `-D <ruta>`.

**El archivado fallando llena el disco.** Si `archive_command` falla, PostgreSQL
deja de reciclar los segmentos para no perderlos. `pg_wal` crece hasta llenar
`/srv` y el motor se detiene. Es lo que vigila `vigilar-pitr.sh` con los
umbrales 2, 3 y 4. Si llega un aviso de éstos, es urgente.

**El uid 999.** `install -d -o 999` falla en el anfitrión porque ese uid no
tiene nombre. Hay que usar `mkdir` + `chown 999:999`.

**`storageKey` va SIN el prefijo del espacio.** Al insertar filas de
`InvoiceAttachment` a mano es el error fácil de cometer. `buildKey()` devuelve
`<scope>/<año>/<mes>/<id>/<archivo>`; el prefijo `adjuntos/` lo añade
`conPrefijo()` dentro de `getStorage()`, al escribir **y al leer**. Así que el
objeto vive en `adjuntos/facturas/…` pero la columna guarda `facturas/…`.
Guardarla con el prefijo hace que el visor busque `adjuntos/adjuntos/facturas/…`
y muestre el icono de archivo bloqueado, sin más pista. Pasó el 4-sep-2026 con
los adjuntos de prueba.

**Un respaldo que no se ha probado a restaurar no es un respaldo.** Por eso el
ensayo es mensual y automático, no una nota en la documentación.

---

## 8 · Cómo se verificó

El 4 de septiembre de 2026, con datos reales:

1. En la base `postgres` (nunca en `maryun_erp`) se creó una tabla de juguete.
2. Se insertó `ANTES del corte` a las `01:32:32-04`.
3. Se tomó el instante de corte: `01:32:40-04`.
4. Se insertó `DESPUES del corte` a las `01:32:57-04`.
5. Se restauró a `01:32:40-04` en una instancia aparte.

Resultado: la instancia restaurada contenía **sólo** la primera fila, con
21.904 documentos del SII intactos. El registro de PostgreSQL lo confirma:

```
LOG:  recovery stopping before commit of transaction 27655, time 2026-09-04 01:32:57.370784-04
LOG:  last completed transaction was at log time 2026-09-04 01:32:32.189946-04
```

El corte cae exactamente donde se pidió. La tabla de prueba se borró después.

---

## 9 · Lo que sigue sin cubrir

- **ClickHouse** no tiene PITR y no lo va a tener. Su ventana sigue siendo de
  24 horas.
- **Los adjuntos del ERP** (XML del SII, imágenes) van por otro camino:
  `maryun-archivos.timer`, **cada hora** a R2. Un volcado de PostgreSQL devuelve
  filas, no devuelve un objeto borrado de un bucket. Se bajó de 6 h a 1 h el
  mismo día que se montó el PITR: con la base en una ventana de un minuto, los
  adjuntos pasaban a ser el eslabón lento de la cadena. Cada corrida es
  incremental y tarda 1-2 segundos.

  **Verificado el 4-sep-2026** con `/srv/bin/probar-adjuntos.py`: se subieron 4
  adjuntos (2 a facturas de compra, 2 a nóminas de prueba en BORRADOR), el
  respaldo los recogió los 4 bajo `adjuntos/`, y al bajarlos del bucket de
  respaldo el sha256 de cada uno coincidía con el `checksum` guardado en
  `InvoiceAttachment`. El PDF seguía siendo un PDF y el PNG un PNG.

  Lo que ese script **no** prueba: la ruta HTTP `/api/adjuntos` ni sus permisos.
  Esas rutas exigen sesión de NextAuth y no hay token de API; simularlas
  obligaría a forjar una sesión. El script replica la lógica de
  `addAttachment()` —`sanitize`, `buildKey`, sha256— y sube con rclone, que
  firma SigV4 igual que el driver. Para borrar lo que creó:
  `sudo /srv/bin/probar-adjuntos.py --limpiar --hazlo`.
- **El respaldo diario sigue existiendo** y debe seguir existiendo: el PITR
  protege de la corrupción y del error humano dentro de la ventana de retención,
  pero un volcado lógico es lo que salva de un fallo de formato del propio motor
  o de un error de versión.
