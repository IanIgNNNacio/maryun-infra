# Preview del ERP

Cómo se mantiene `preview.maryun.cl` con datos reales. Montado el 4 de
septiembre de 2026.

---

## 1 · Qué hace

Cada día a las 05:10 UTC, `maryun-preview.timer` copia producción a preview:

```
  maryun-erp-db     ──(pg_dump | pg_restore)──►  maryun-erp-preview-db
  R2 maryun-erp     ──(rclone sync)──────────►  R2 maryun-erp-preview
                                                        │
                                                        ▼
                                        se reinicia la aplicación de preview
```

Tarda unos **8 segundos** de punta a punta: 289 MB de base y el bucket completo.
Las dos instancias viven en la misma máquina, así que la copia no sale de ella.

### Son dos instancias de PostgreSQL, no dos bases

`maryun-erp-preview-db` es un motor aparte, en `10.8.0.1:5435`, **sin
`archive_mode`**.

Al principio preview era otra base dentro de `maryun-erp-db`. El problema es que
**el WAL es del cluster entero, no de una base**: cada refresco recrea preview
por completo, y eso son **193 MB de WAL medidos**. A una pasada por hora, 4,5
GB/día entrando en el archivo del PITR y saliendo cifrados a R2 — gigabytes al
mes para poder recuperar una copia desechable, restauraciones más lentas, y los
umbrales de `vigilar-pitr.sh` rozándose cada hora.

Medido después de separarlas: **un refresco completo genera 0 MB de WAL en
producción**.

El respaldo de preview es el propio refresco. Si esa instancia se pierde, se
reconstruye en 8 segundos.

```bash
sudo /srv/bin/refrescar-preview.sh            # refrescar ahora
sudo /srv/bin/refrescar-preview.sh --forzar   # aunque esté congelado
```

## 2 · Congelar preview mientras pruebas

Un refresco **borra la base de preview entera**. Si estás probando algo con
datos que no quieres perder al día siguiente:

```bash
sudo touch /srv/PREVIEW-CONGELADO   # el refresco se salta y lo deja escrito
sudo rm /srv/PREVIEW-CONGELADO      # vuelve a refrescar en la hora siguiente
```

---

## 3 · Las decisiones que hay detrás

### Se copia todo, sin anonimizar

Decisión de Ian, 4-sep-2026. El razonamiento: preview y producción **usan el
mismo login**, el control de acceso es idéntico, y a preview entran dos
personas. Copiar en claro no amplía la exposición, y anonimizar RUT y nombres
haría que las pruebas dejaran de reproducir casos reales.

Si algún día preview se abre a más gente, esta decisión hay que revisarla:
entonces sí serían datos de clientes y proveedores en un entorno con otro
perímetro.

### `sync` en el bucket, al contrario que en el respaldo

| destino | comando | por qué |
|---|---|---|
| bucket de respaldo | `copy`, **nunca** `sync` | un borrado en producción no debe propagarse al respaldo |
| bucket de preview | **`sync`** | preview tiene que ser un espejo |

Con `copy`, el bucket de preview acumularía objetos huérfanos: archivos subidos
en pruebas viejas cuyas filas ya no existen en la base recién restaurada. En un
respaldo eso es exactamente lo que se quiere; en un espejo, basura.

### Se reinicia la aplicación al final, y no es cosmética

La restauración trae también el `_prisma_migrations` de producción. Si la rama
de preview tiene migraciones que producción todavía no tiene, quedarían sin
aplicar. El reinicio las vuelve a aplicar porque **el entrypoint de la imagen
ejecuta `prisma migrate deploy` al arrancar** (`docker/arrancar.sh`).

Verificado en la primera corrida: preview tenía dos migraciones propias
—`20260915000000_ajuste_origen_caja` y `20260915010000_ajuste_desde_cobro`— y el
reinicio las aplicó sobre los datos recién copiados:

```
All migrations have been successfully applied.
[arrancar] base al día, iniciando la aplicación
```

Al revés no pasa: **preview siempre va por delante de producción**, nunca al
contrario. Es la dirección natural del flujo —se prueba en preview y luego se
promueve— y por eso el reinicio basta: sólo hay que aplicar hacia adelante.

### `DIRECT_URL` también apunta a la base, y es fácil olvidarla

El `schema.prisma` declara dos conexiones:

```prisma
url       = env("DATABASE_URL")  // runtime
directUrl = env("DIRECT_URL")    // migraciones
```

Es una herencia de Neon, donde la conexión agrupada y la directa eran hosts
distintos. Al mover preview de instancia se cambió `DATABASE_URL` y **no**
`DIRECT_URL`, así que la aplicación leía de la instancia nueva pero
`prisma migrate deploy` seguía yendo a la de producción: **cada reinicio de
preview volvía a crear ahí una base `maryun_erp_preview` vacía**. Se borraba y
reaparecía sola.

Si algún día se vuelve a mover la base, hay que cambiar **las dos**.

### El contenedor se busca por su `DATABASE_URL`

Coolify le pone un sufijo aleatorio al nombre y cambia en cada despliegue, así
que el script recorre los contenedores en marcha y se queda con el que tiene
`maryun_erp_preview` en su `DATABASE_URL`. Buscarlo por nombre se rompería en el
siguiente despliegue.

### Los guardarrailes

El script **borra una base de datos entera**, así que antes de tocar nada
comprueba literalmente que el destino se llama `maryun_erp_preview` y que no
coincide con el origen, y que el bucket de preview no es el de producción. Un
despiste con una variable no puede acabar en producción.

`DROP DATABASE ... WITH (FORCE)` corta las conexiones abiertas de la aplicación
de preview; sin eso el `DROP` se queda esperando indefinidamente a que las
suelte.

---

## 4 · Lo que se sustituyó

En el VPS viejo esto lo hacía `scripts/replica/sync.sh`, en el repositorio del
ERP, instalado en cron cada hora. Su cabecera lo describe:

> «Trae producción oficial (Vercel + Neon) al VPS y deja la copia de seguridad
> por el camino […] (opcional) preview»

Dependía de Neon y de Vercel, que ya no existen, y **nunca se instaló en este
servidor**. Entre la migración del 1 de septiembre y hoy, preview estuvo
congelada en la instantánea con la que se creó: 210 MB frente a los 289 MB de
producción.

---

## 5 · Respaldos de preview

| | |
|---|---|
| Base `maryun_erp_preview` | **no se respalda**, y es correcto: está en su propia instancia sin archivado y el refresco la reconstruye en 8 segundos |
| Bucket `maryun-erp-preview` | **no se respalda**, por lo mismo |

**Corrección del 5-sep-2026:** aquí decía que la base de preview entra en el
volcado diario. **No entra.** `respaldo.sh` recorre una lista fija de
contenedores —`maryun-erp-db`, `coolify-db`, `mage-db`, `metabase-db`,
`superset-db`— y la instancia nueva no está en ella. Es lo correcto para algo
que se reconstruye en ocho segundos, pero convenía no dejarlo dicho al revés.

Ver [`pitr.md`](pitr.md) para el detalle de los respaldos de la base.
