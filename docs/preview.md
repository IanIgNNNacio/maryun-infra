# Preview del ERP

Cómo se mantiene `preview.maryun.cl` con datos reales. Montado el 4 de
septiembre de 2026.

---

## 1 · Qué hace

Cada hora, en el minuto 40, `maryun-preview.timer` copia producción a preview:

```
  maryun_erp        ──(pg_dump | pg_restore)──►  maryun_erp_preview
  R2 maryun-erp     ──(rclone sync)──────────►  R2 maryun-erp-preview
                                                        │
                                                        ▼
                                        se reinicia la aplicación de preview
```

Tarda unos **9 segundos** de punta a punta: 289 MB de base y el bucket
completo. Las dos bases viven en el mismo PostgreSQL, así que la copia no sale
de la máquina.

```bash
sudo /srv/bin/refrescar-preview.sh            # refrescar ahora
sudo /srv/bin/refrescar-preview.sh --forzar   # aunque esté congelado
```

## 2 · Congelar preview mientras pruebas

Un refresco **borra la base de preview entera**. Si estás probando algo con
datos que no quieres perder al cambiar la hora:

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

Al revés también pasa: si producción va por delante, preview corre código viejo
sobre un esquema nuevo. Con migraciones aditivas eso funciona; con una que
renombre o borre una columna, no. Si preview se rompe justo después de un
refresco, mirar ahí primero.

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
| Base `maryun_erp_preview` | cubierta: va en el mismo cluster, así que la protegen el PITR y el volcado diario |
| Bucket `maryun-erp-preview` | **no se respalda**, y es correcto: se regenera solo en el refresco siguiente |

Ver [`pitr.md`](pitr.md) para el detalle de los respaldos de la base.
