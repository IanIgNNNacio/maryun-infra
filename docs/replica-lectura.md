# La réplica de lectura del ERP

Una copia de solo lectura de `maryun_erp` que se mantiene sola, para que los
tableros no consulten la base que atiende a las personas. Montada el 8 de
septiembre de 2026.

---

## 1 · Por qué existe

El 7 de septiembre, **una sola pantalla del ERP, con un usuario**, agotó la
memoria compartida del Postgres de producción: el tablero de ventas lanzaba ocho
agregaciones simultáneas sobre 1,27 millones de líneas y llenaba `/dev/shm`. Y
Metabase, por su parte, consultaba `maryun_erp` en directo.

Los dos síntomas del mismo problema: **la base que factura estaba haciendo
trabajo analítico**.

## 2 · Qué es

```
  maryun-erp-db  ──(replicación en flujo, ~124 ms)──►  maryun-erp-replica
   10.8.0.1:5433                                        10.8.0.1:5436
   escribe y lee                                        SOLO LECTURA
```

**Es de solo lectura por construcción, no por permisos.** Un *hot standby* de
PostgreSQL rechaza cualquier escritura venga de quien venga:

```
ERROR: cannot execute CREATE TABLE in a read-only transaction
```

Ni el superusuario puede estropear datos desde ahí. Comprobado.

**Por qué replicación en flujo y no un ETL:** no hay catálogo de tablas que
mantener ni desfase que explicar. Lo que entra en el primario aparece aquí en
milisegundos —124 ms medidos—, y una consulta absurda la aguanta esta máquina.

**Ojo:** la replicación física copia el **cluster entero**, así que la réplica
también tiene `maryun_ecommerce` y `maryun_ecommerce_preview`.

## 3 · Quién la usa

| | apunta a |
|---|---|
| Metabase → «DWH facturas RCV» (id 4) | **`maryun-erp-replica`**, esquema `dwh` |
| Metabase → «ERP maryun (réplica)» (id 6) | **`maryun-erp-replica`**, los tres esquemas |
| Metabase → «Postgres espejo MySis» | `dwh-postgres` (viene de ClickHouse, otra cosa) |
| Superset | ClickHouse y el espejo |

En «DWH facturas RCV» se repuntó **la misma fila** de Metabase en vez de crear
otra, así que todas las preguntas y tableros guardados siguen funcionando sin
tocarlos. Su filtro de esquemas (`inclusion: dwh`) se dejó como estaba: expone
las cinco vistas del RCV y nada más, que es lo que su nombre promete.

«ERP maryun (réplica)» se creó aparte el 8-sep-2026, **sin filtro de
esquemas**: 279 tablas —249 de `public`, 25 de `mig`, 5 de `dwh`—. Son las
tablas crudas de Prisma, en `PascalCase` y con claves foráneas por UUID; sirven
para explorar, no para construir tableros que tengan que durar. Lo que se
consulte a menudo conviene curarlo como vista en `dwh`.

**Metabase ya no consulta producción por ninguna de las dos.** Para conectarse
a mano:

| | dwh_lector | erp_lector |
|---|---|---|
| Host / puerto | `10.8.0.1:5436` (con la VPN) | igual |
| Base | `maryun_erp` | igual |
| Alcance | sólo el esquema `dwh` | `public`, `mig` y `dwh` |
| Credencial | `/srv/secrets/dwh-lector.env` | `/srv/secrets/erp-lector.env` |

**Los dos roles se crean en el PRIMARIO**, nunca aquí: una réplica no admite
`CREATE ROLE`. La replicación física los trae en milisegundos, igual que los
datos.

**Quién ve qué en Metabase (8-sep-2026):** el grupo «All Users» tiene
`view-data: unrestricted` y `create-queries: query-builder` sobre la nueva
conexión, así que los cinco usuarios pueden armar preguntas sobre cualquiera de
las 279 tablas desde la interfaz. Sólo los administradores —Ian y Felipe— tienen
SQL nativo. Las tablas con credenciales (`UserCredential`, `PortalAccount`,
`ApiKey`, `TotpCredential`, `SiiCesion`, `mail_connection`) estaban **vacías**
al montar esto; cuando dejen de estarlo hay que revocarles el `SELECT` a
`erp_lector`, que es una línea:

```sql
revoke select on "UserCredential", "PortalAccount", "ApiKey",
                 "TotpCredential", "SiiCesion", mail_connection
  from erp_lector;   -- en el PRIMARIO
```

## 4 · Lo que hay que vigilar, y es una cosa

**Un slot de replicación puede llenar el disco.** El slot obliga al primario a
conservar el WAL hasta que la réplica lo consuma; si la réplica muere y el slot
se queda, `pg_wal` crece sin freno hasta detener el motor. Es el mismo modo de
fallo que un `archive_command` roto.

Aquí hay dos redes contra eso:

1. **`max_slot_wal_keep_size = 10GB`** en el primario. Pasado ese techo el slot
   se invalida en vez de llenar el disco. Producción sigue viva; la que se queda
   atrás es la réplica.
2. **`restore_command`** en la réplica, apuntando al archivo de pgBackRest. Si
   el slot se invalida, se pone al día desde ahí en vez de exigir una copia base
   nueva. Por eso este contenedor usa la imagen con `pgbackrest` dentro.

`vigilar-pitr.sh` comprueba el slot cada 10 minutos y avisa por Telegram si está
inactivo o si perdió su reserva.

```bash
sudo /srv/bin/vigilar-pitr.sh --verboso
```

## 5 · Comprobar que está viva

```bash
# desde la réplica: ¿está recibiendo?
sudo docker exec maryun-erp-replica psql -U maryun -d maryun_erp -c \
  "select pg_is_in_recovery(), status, sender_host from pg_stat_wal_receiver"

# desde el primario: ¿cuánto va por detrás?
sudo docker exec maryun-erp-db psql -U maryun -d postgres -c \
  "select application_name, state, pg_size_pretty(pg_wal_lsn_diff(sent_lsn, replay_lsn)) from pg_stat_replication"
```

En operación normal el retraso es **0 bytes**.

## 6 · Si hay que rehacerla

No tiene respaldo propio ni lo necesita: es una copia. Se reconstruye desde el
primario, y el primario no se entera más que de una lectura secuencial de
1,5 GB.

```bash
sudo docker compose -f /srv/stacks/maryun-erp-replica/docker-compose.yml down
sudo rm -rf /srv/stacks/maryun-erp-replica/db
sudo docker exec maryun-erp-db psql -U maryun -d postgres \
  -c "select pg_drop_replication_slot('replica_metabase')"
# y repetir el pg_basebackup con -R -C -S replica_metabase
```

## 7 · Trampas que costaron tiempo al montarla

**No dejes archivos ajenos dentro de `PGDATA`.** `pg_basebackup` copia el
directorio entero y falla si encuentra algo que el usuario `postgres` no puede
leer. Aquí murió con `could not open file "./pg_hba.conf.antes-replica":
Permission denied`, por una copia de seguridad que se dejó ahí con propietario
`root`. Las copias de configuración van fuera, en
`/srv/stacks/maryun-erp/respaldos-config/`.

**`host all all all` en `pg_hba.conf` NO cubre la replicación.** La palabra
`replication` en la columna de base de datos es literal, no un comodín: hace
falta una línea propia. Se recarga con `pg_reload_conf()`, sin reiniciar.

**`max_connections` de la réplica tiene que ser ≥ el del primario.** PostgreSQL
se niega a arrancar una réplica con menos plazas: *«recovery aborted because of
insufficient parameter settings»*. Lo mismo vale para `max_worker_processes`,
`max_locks_per_transaction` y `max_prepared_transactions`.

**`active::text` devuelve `true`, no `t`.** Comparar con `"t"` en un guion de
vigilancia dispara una alarma falsa con la réplica perfectamente conectada. Pasó
al escribir esta misma vigilancia.

## 8 · Lo que esto NO arregla

**El tablero del propio ERP sigue consultando producción.** La aplicación
consulta la base que tiene configurada; un espejo no cambia eso. Para que
`/reportes/ventas` fuera a la réplica haría falta un cambio de código —una
conexión de solo lectura para las pantallas analíticas—, y eso es una decisión
aparte.

Lo que sí lo alivió por ahora es el arreglo `cd71cd9` en el repositorio del ERP,
que bajó el pico de memoria compartida de ese tablero de 1008 MB a 6,7 MB. Está
en `main` y **todavía no en `production`**.
