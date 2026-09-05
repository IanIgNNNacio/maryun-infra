# Emergencia — qué hacer cuando algo se cae

Para cualquiera que tenga que actuar, no sólo para quien montó esto.

> **Este documento vive en GitHub a propósito.** Si el servidor está muerto, la
> documentación que estaba dentro del servidor también lo está. Aquí se lee
> desde cualquier parte: `github.com/IanIgNNNacio/maryun-infra`, en `docs/`.
>
> Las **llaves** no están aquí ni pueden estarlo. Están en el gestor de
> contraseñas de Ian, copiadas de `/srv/secrets/RECUPERACION.txt`. Sin ellas los
> respaldos de fuera son ilegibles. Si estás leyendo esto en una emergencia y no
> tienes las llaves, lo primero es conseguirlas.

---

## 0 · Qué se cayó

Empieza por aquí. Cada rama lleva a una sección distinta y **el orden importa**:
no restaures nada antes de saber qué pasó.

```
¿Responde https://erp.maryun.cl ?
│
├─ NO ──► ¿Responde el servidor?   ssh -p 2222 ialrringo@148.113.168.13
│         ├─ SÍ ──► §1  una aplicación caída, el servidor está bien
│         └─ NO ──► ¿Vuelve en unos minutos?
│                   ├─ SÍ ──► §3  caída temporal
│                   └─ NO ──► §4  el servidor está muerto: reconstruir
│
└─ SÍ, pero los datos están mal
   (borrado accidental, migración equivocada, cifras absurdas)
   ──► §2  volver a un instante anterior
```

**Lo que ya no aplica.** Hasta agosto de 2026 la aplicación vivía en Vercel y la
base en Neon, en sitios distintos, y el procedimiento era «si cae Vercel, pasar
al VPS de `respaldo.maryun.cl`». **Nada de eso existe.** Hoy todo está en una
sola máquina, así que no hay a dónde «pasarse»: o se arregla el servicio, o se
reconstruye en otra máquina.

---

## 1 · Una aplicación caída, el servidor está bien

Lo más común, y lo menos grave: la base está intacta.

```bash
ssh -p 2222 ialrringo@148.113.168.13
sudo docker ps -a | grep -v Up          # qué está caído
```

Redesplegar desde Coolify (`coolify.maryun.cl`, **sólo por VPN**), o por API:

```bash
# uuid de cada aplicación
sudo docker exec coolify-db psql -U coolify -d coolify -tAF' | ' \
  -c "select name, uuid, git_branch from applications order by id"

sudo python3 /srv/bin/coolify-api.py POST "/api/v1/deploy?uuid=<uuid>&force=true" '{}'
```

Si es la base la que no arranca, mira su registro antes de tocar nada:

```bash
sudo docker logs --tail 50 maryun-erp-db
```

**No borres el directorio de datos.** `/srv/stacks/maryun-erp/db` es la base
misma, no una caché.

---

## 2 · Volver a un instante anterior (PITR)

Para borrados accidentales, una migración equivocada, o datos que dejaron de
cuadrar. **Sólo cubre `maryun_erp`.**

**La ventana es de un minuto**: se archiva WAL de forma continua, y se conservan
**30 días**.

### Primero: comprobar en una copia, sin tocar producción

```bash
sudo /srv/bin/pitr.sh info                          # hasta dónde se puede volver
sudo /srv/bin/pitr.sh ensayo "2026-09-04 01:32:40-04"
```

Eso levanta un PostgreSQL aparte en `127.0.0.1:5439` con los datos de ese
instante. Producción **no se toca**: el repositorio se monta de sólo lectura.

```bash
sudo docker exec -it pitr-ensayo psql -U maryun -d maryun_erp
```

Mira ahí si el instante elegido es el bueno. Si no, repite con otra hora.

> **La hora es la de Chile** (`America/Santiago`). Escribe el desfase explícito
> —`-04` en invierno, `-03` en verano— para que no haya ambigüedad.

### Después: recuperar producción

El procedimiento completo, con sus advertencias, está en
[`pitr.md`](pitr.md) §6 «Runbook: restaurar», caso B. En resumen: parar lo que
escribe, apartar el directorio actual **sin borrarlo**, restaurar, arrancar, y
hacer un `full` en cuanto se estabilice.

**No borres `docker.antes-de-restaurar` hasta haber comprobado todo.**

---

## 3 · Caída temporal del servidor

Ya pasó dos veces (1 y 2 de septiembre de 2026): reinicios en frío sin rastro en
el sistema operativo, de unos dos minutos. El servidor volvió solo las dos.

**No hay nada que hacer desde fuera.** No intentes reconstruir por una caída de
minutos: es mucho más peligroso que esperar.

- Estado del servidor: panel de OVH.
- Vigilancia externa: el workflow de GitHub Actions de este mismo repositorio
  comprueba los cuatro dominios cada 30 minutos e **incluye la caída en su
  registro aunque el servidor esté apagado**, que es justamente lo que Uptime
  Kuma no puede hacer porque vive dentro.
- Si se repite, está redactado el ticket para OVH:
  [`ticket-ovh-reinicios.md`](ticket-ovh-reinicios.md).

Al volver, comprueba que el archivado de WAL siguió bien:

```bash
sudo /srv/bin/vigilar-pitr.sh --verboso
```

---

## 4 · El servidor está muerto: reconstruir

Lo más grave, y lo que hay que poder hacer sin improvisar.

### Qué necesitas antes de empezar

1. **`RECUPERACION.txt`**, del gestor de contraseñas de Ian. Trae la clave `age`
   y la passphrase de rclone. **Sin ellas no se puede continuar.**
2. Las credenciales de OVH Backup Storage y de Cloudflare R2 — están en ese
   mismo documento.
3. Una máquina con Docker y espacio: unos 700 MB por generación, más lo que
   crezca al restaurar.

### Qué hay guardado, y dónde

| dónde | qué | cifrado | retención |
|---|---|---|---|
| **OVH Backup Storage**, `ovh:maryun01-v2` | el respaldo diario completo: volcados de los seis Postgres, ClickHouse, pipelines de Mage, configuración de Coolify y `/srv/secrets` | **sí**, doble: `age` sobre los secretos y `rclone crypt` sobre todo lo que sale | 30 días |
| **R2**, `maryun-erp-respaldo/pitr/` | el repositorio de PITR: WAL y copias base de `maryun_erp` | **sí**, `rclone crypt` | 30 días |
| **R2**, `maryun-erp-respaldo/erp/` | copia de los adjuntos del ERP | no | se acumula |
| **R2**, `maryun-erp` | los adjuntos **vivos**: XML del SII, imágenes | no | — |

**Los adjuntos no dependen del servidor.** Viven en R2, que es de Cloudflare. Si
el servidor desaparece siguen estando: no hay que restaurarlos, sólo apuntar la
aplicación nueva al mismo bucket.

### Los pasos

**1 · Bajar la última generación de OVH.** Es `respaldo-externo.sh` al revés:
mismos remotos, `copy` en el otro sentido.

```bash
# ver qué generaciones hay
docker run --rm \
  -e RCLONE_CONFIG_OVH_TYPE=ftp \
  -e RCLONE_CONFIG_OVH_HOST="<OVH_HOST>" \
  -e RCLONE_CONFIG_OVH_USER="<OVH_USER>" \
  -e RCLONE_CONFIG_OVH_PASS="$(docker run --rm rclone/rclone obscure '<OVH_PASS>')" \
  -e RCLONE_CONFIG_OVH_EXPLICIT_TLS=true \
  -e RCLONE_CONFIG_CIFRADO_TYPE=crypt \
  -e RCLONE_CONFIG_CIFRADO_REMOTE="ovh:maryun01-v2" \
  -e RCLONE_CONFIG_CIFRADO_PASSWORD="$(docker run --rm rclone/rclone obscure '<RCLONE_CRYPT_PASSWORD>')" \
  -e RCLONE_CONFIG_CIFRADO_FILENAME_ENCRYPTION=standard \
  rclone/rclone lsd cifrado:

# y bajar la que elijas (los nombres son sellos de tiempo: 2026-09-04T2300)
#   ... mismas variables ... rclone copy cifrado:<sello> /destino/<sello>
```

**2 · Comprobar que llegó entero.** Cada generación trae su `MANIFIESTO.txt` con
las sumas SHA-256:

```bash
cd /destino/<sello> && sha256sum -c MANIFIESTO.txt
```

**3 · Abrir los secretos.** Van cifrados aparte con `age`:

```bash
age -d -i <clave-privada-age> secretos/srv-secrets.tar.gz.age | tar -xzf -
```

Eso devuelve `/srv/secrets` entero: las credenciales de todos los servicios.

**4 · Restaurar las bases.** Un volcado por base, en formato `custom`:

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=... postgres:18.6
docker exec -i pg pg_restore -U postgres -d maryun_erp --no-owner --no-privileges \
  < postgres/maryun-erp-db--maryun_erp.dump
```

Las que importan, por orden: `maryun_erp` (el ERP), `coolify` (para recuperar
los despliegues y sus variables), `mage` y `superset`/`metabase`.

**5 · ClickHouse**, si hace falta el almacén analítico. Los `.zip` de
`clickhouse/` son respaldos nativos:

```sql
RESTORE DATABASE dwh FROM Disk('backups', 'dwh.zip')
```

**6 · Los pipelines de Mage.** `archivos/mage-pipelines.tar.gz`. **Es lo más
irreemplazable del servidor**: no existen en ningún repositorio git.

**7 · Si necesitas un instante concreto**, y no el del volcado, baja también el
repositorio de PITR desde R2 (`maryun-erp-respaldo/pitr/`, con la misma
passphrase de rclone), ponlo en `/srv/pitr` y sigue [`pitr.md`](pitr.md) §6
caso C.

### El orden importa

Levanta primero **la base del ERP**; es lo que sostiene la operación. Coolify,
Mage y el BI pueden esperar. ClickHouse es el último: se puede reconstruir desde
MySis si hiciera falta.

---

## 5 · Lo que no está cubierto, y conviene saberlo

- **MySis** es un sistema aparte y no se respalda desde aquí. Sigue siendo la
  fuente de los datos de ventas.
- **Preview** no se respalda a propósito: su base se reconstruye desde
  producción en ocho segundos con `/srv/bin/refrescar-preview.sh`, y su bucket
  se vuelve a sincronizar en el mismo paso. Tampoco entra en el volcado diario:
  `respaldo.sh` recorre una lista fija de contenedores y ése no está.
- **ClickHouse no tiene recuperación a un punto en el tiempo.** Su ventana es el
  respaldo diario, 24 horas.
- **El VPS viejo** (`51.222.28.249`) estuvo comprometido. No es un plan de
  respaldo: no lo enciendas ni lo uses para nada.

---

## 6 · Comprobar que esto funciona, antes de necesitarlo

Un respaldo que no se ha probado a restaurar no es un respaldo.

| qué | cada cuánto | quién |
|---|---|---|
| Restauración real del PITR | primer domingo de mes | automático, `maryun-pitr-ensayo.timer` |
| Que las copias salen del servidor | cada 15 min a R2, diario a OVH | automático, avisa por Telegram si falla |
| Bajar una generación de OVH y abrirla | **nadie lo hace todavía** | pendiente: conviene hacerlo una vez a mano |

Ese último punto es el hueco real. El PITR se ensaya solo; la reconstrucción
completa desde OVH **nunca se ha probado de principio a fin**. Hacerlo una vez,
con calma, vale más que cualquier documento — incluido éste.

---

## 7 · A quién avisar

- **Ian** — administrador del servidor, tiene las llaves.
- **OVH** — para el hardware y el Backup Storage.
- **Cloudflare** — para DNS, el proxy y R2.
