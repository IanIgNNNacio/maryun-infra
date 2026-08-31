# Recomendaciones de producción — maryun01

Decisiones pendientes y recomendaciones acumuladas durante la migración del VPS
al servidor dedicado. Escrito el 31-ago-2026. Cada punto dice **qué**, **por
qué** y **qué falta**.

---

## 1. Respaldos

### Qué respalda hoy `respaldo.sh`

Es un respaldo **del servidor completo**, no solo del ERP:

| Fuente | Contenido |
|---|---|
| PostgreSQL × 6 bases | `maryun_erp`, `maryun_erp_preview`, `coolify`, `mage`, `maryun_db` (Metabase), `superset` |
| ClickHouse × 3 bases | `dwh`, `logistica`, `logistica_v2` (comando `BACKUP` nativo) |
| Pipelines de Mage | `/srv/stacks/mage/project` — **lo único que no existe en ningún git** |
| Configuración | Coolify y ClickHouse |
| `/srv/secrets` | cifrado con la clave pública de `age` |

Total actual: ~660 MB por corrida. Retención local de 14 días.

**Lo que NO cubre:** los adjuntos del ERP. Esos nunca tocan el disco del
servidor — el ERP los sube directo a Cloudflare R2. Son una capa aparte.

### Capa 3 — la copia fuera del servidor: HECHA (31-ago-2026)

Los respaldos ya no viven solo en el mismo RAID que los datos. `respaldo-externo.sh`
los empuja al **Backup Storage de OVHcloud** (500 GB incluidos con el servidor),
**cifrados en origen**.

| | |
|---|---|
| Destino | `ftpback-bhs5-39.mybackup.ovh.ca` sobre FTPS |
| Cifrado | capa `crypt` de rclone: contenidos **y nombres de archivo** |
| Modo | `copy`, nunca `sync` — un borrado local no se propaga |
| Montaje | ninguno: empuja y se desconecta |
| Retención | 30 días en destino, 14 en local |
| Programación | `maryun-respaldo.timer`, 03:15 ± 20 min |

Primera corrida verificada: 14 objetos, 692 MB.

**Sin la clave de cifrado este respaldo es irrecuperable.** Vive en
`/srv/secrets/RECUPERACION.txt` y está **excluida del respaldo a propósito** —
una llave no se guarda dentro de lo que abre. Ian debe tener una copia fuera del
servidor.

Detalles que costaron y conviene no reaprender:

- El nombre del servidor FTPS es **específico de la región**: `.mybackup.ovh.ca`
  para Canadá, no `.ovh.net`.
- OVH admite **tres conexiones simultáneas**. No basta bajar `--transfers`:
  rclone abre además 8 verificadores y su propio grupo de conexiones. Hay que
  limitar los tres (`concurrency=2`, `--transfers 2`, `--checkers 1`). Al
  excederlo el servidor responde en texto plano y rclone reporta
  `tls: first record does not look like a TLS handshake`, que apunta al sitio
  equivocado.

### Capa 4 — verificación: PARCIAL

**Hecho:** cada corrida ejecuta `rclone check` (compara tamaños y sumas) y una
prueba manual de recuperación completa confirmó el ciclo de ida y vuelta —
archivo bajado del destino cifrado, SHA-256 idéntico al del manifiesto, `tar`
válido con 5.238 archivos dentro.

**Falta:** que esa prueba sea **automática y periódica**. Hoy `check` demuestra
que los bytes llegaron; no que un volcado de PostgreSQL restaure. La prueba de
verdad es levantar un contenedor desechable, restaurar el volcado más reciente y
contar filas. Es la capa que casi nadie hace y la única que demuestra que las
otras tres sirven.

### Respaldo de adjuntos

`archivos.sh` copia `maryun-erp` → `maryun-erp-respaldo` con `rclone copy`
(nunca `sync`), así un borrado no se propaga. Sigue en el VPS viejo; **hay que
portarlo a maryun01**.

**Recomendación adicional: poner un bloqueo de bucket en `maryun-erp-respaldo`.**

R2 **no tiene versionado de objetos** (lo verifiqué en su documentación). Pero
tiene algo mejor para este caso: **bloqueos de bucket**, que impiden borrar y
sobrescribir objetos durante un plazo definido — 90 días, hasta una fecha, o
indefinidamente. Es inmutabilidad real.

La diferencia frente al versionado es grande: con versiones, alguien con
credenciales puede borrarlas todas. Con un bloqueo **no puede — ni tú**. Protege
contra el escenario que más importa: alguien obtiene el token del bucket de
respaldo y borra los respaldos antes de hacer daño.

Detalles: los bloqueos prevalecen sobre las reglas de ciclo de vida, un bucket
con bloqueo activo no se puede vaciar, y si hay varias reglas gana la más
estricta.

Se configura en: R2 → bucket → *Settings* → *Bucket lock rules*.

### Nota técnica sobre deduplicación

`restic` deduplica mal sobre archivos ya comprimidos, y los volcados actuales
salen comprimidos (`pg_dump -Fc`, los `.zip` de ClickHouse). Cada respaldo sube
casi entero en vez de solo los cambios.

Hoy no importa. Pasando los 50 GB, conviene volcar **sin comprimir** y dejar que
restic comprima y deduplique.

---

## 2. Recuperación a punto en el tiempo del ERP

Al salir de Neon **desapareció su PITR**, que era la capa 1 del diseño anterior
— la que cubre el `DELETE` sin `WHERE`. Los volcados cada N horas no la
reemplazan: entre uno y otro se pierde todo.

**Recomendación: archivado de WAL con pgBackRest** sobre el Postgres del ERP.
Permite volver a cualquier instante, no solo al último volcado.

No es urgente **mientras los datos sean de prueba**. Sí lo es **antes de meter
datos reales**.

---

## 3. Monitoreo y alertas: HECHO (31-ago-2026), falta el canal de avisos

Instalados **Uptime Kuma** (13 monitores) y **Beszel** (métricas + 5 reglas de
saturación), ambos solo por VPN. Detalle completo en
[monitoreo.md](monitoreo.md).

**Lo que queda: por dónde llegan los avisos.** Las reglas están puestas pero sin
canal de salida no notifican a nadie. Y hay una limitación que ningún canal
resuelve por sí solo:

> Uptime Kuma y Beszel corren **dentro** de maryun01. Si el servidor entero se
> cae, el monitoreo se cae con él y nadie avisa.

Cubrirlo exige algo **fuera** del servidor que espere una señal periódica.

---

## 4. Seguridad — pendientes

| Qué | Estado |
|---|---|
| Ubuntu Pro + **Livepatch** (gratis hasta 5 máquinas) | falta. Da parches de kernel sin reiniciar |
| Sacar `ubuntu` de `AllowUsers` en SSH | falta. Es el usuario que todos los bots prueban |
| **Revocar el token de API de Coolify** y volver a deshabilitar su API | falta. La API expone las claves privadas en texto plano a cualquier token con lectura |
| Rotar el token de R2 de producción | **hecho** |
| Decidir si `io_uring` queda habilitado | pendiente. Postgres 18 lo aprovecha, pero tiene historial de CVE y **el malware del incidente lo usaba para evadir detección** |
| Considerar **Cloudflare Access** delante del ERP | opcional. Pone una verificación de identidad antes de que la petición llegue al servidor |

### Credenciales que siguen comprometidas

Del incidente del 3-ago-2026 (WordPress → RCE como `www-data` → minero
`Linux.BackDoor.Armada.1`). El proceso podía leer varios archivos en modo 644:

| Credencial | Acción |
|---|---|
| `AUTH_MICROSOFT_ENTRA_ID_SECRET` | rotar en Azure, en el corte |
| `RESEND_API_KEY` y `RESEND_WEBHOOK_SECRET` | rotar en Resend |
| Contraseña personal de Mage y `MAGE_API_KEY` | **la contraseña tiene pinta de personal: cambiarla en cualquier otro servicio donde se reutilice** |
| Credenciales de Superset (`SUPERSET_SECRET_KEY`, etc.) | rotar con `superset re-encrypt-secrets` |
| R2 de producción | ya rotada |

---

## 5. Revisión del stack de datos (después de la migración)

Requisito: **todo open source**.

- **Mage OSS está en modo mantenimiento**: sin release desde `0.9.79`
  (ene-2026), y el commit más reciente es "Sync latest Mage Pro documentation" —
  la empresa pivotó a su producto comercial.
- **59% de los pipelines (39 de 66) son réplica tabla-a-tabla MySis → ClickHouse**:
  29 con tres bloques cada uno y extractores de 3 líneas de SQL. Eso no es
  orquestación, es replicación.
- **CDC está bloqueado**: MySis (MariaDB 10.6.23) tiene `log_bin = OFF` y
  `binlog_format = MIXED`. Habilitarlo exige reiniciar la base de producción del
  ERP en la VM de Azure.
- **Lo accionable sin tocar MySis: `dlt`** — extracción incremental por columna
  cursor, sin binlog, destino ClickHouse nativo. Colapsaría los 39 pipelines en
  una lista de tablas.
- **Sacar Chrome y Playwright de la imagen de Mage**: es lo que la infla de 2 a
  9 GB. Los scrapings deberían ser un servicio aparte.
- **Orquestador** (Airflow, Dagster, Prefect, Kestra) a decidir **al final**,
  cuando queden ~15 pipelines en vez de 66.

Orden: terminar la migración → probar `dlt` en paralelo con 3-4 tablas → retirar
pipelines por tandas → recién entonces evaluar orquestador.

### Herramientas de BI

Metabase y Superset conviven porque **no son redundantes**: entregan la misma
información pero difieren en experiencia de uso, tipos de gráfico y filtros. El
objetivo no es consolidar sino **encontrar una que sea a la vez completa y
amigable**.

Ojo con la exposición: el jefe de Ian usa Metabase bastante, así que dejarlo
solo-VPN puede no ser viable. Posible salida: embeber esos dashboards en el ERP.

### Metabase — con fecha

**El major 60 llega a fin de vida el 2026-09-01.** La 58 es LTS (hasta
feb-2027) pero **no se puede bajar**: las migraciones de esquema de Metabase son
de una sola vía. Hay que **actualizar a la 63 pronto**.

---

## 6. Deuda menor

- **Redes Docker**: `data` y `coolify` se solapan parcialmente. Consolidar.
- **Superset sin Content Security Policy** (`TALISMAN_ENABLED`). Mitigado porque
  es solo-VPN.
- **Swap sin espejo**: son dos áreas de 16 GB independientes, no en RAID 1. Con
  62 GB de RAM y `swappiness=10` no se va a usar, pero se puede crear un volumen
  lógico espejado con los 420 GB libres del grupo LVM.
- **`GRAPH_CLIENT_ID` y `GRAPH_CLIENT_SECRET` vacías**: la recepción de DTE por
  correo puede no estar operativa.
- **Dominios**: al terminar, `respaldo.maryun.cl` queda sin uso — eliminar su URI
  de redirección en Azure y su registro DNS.
