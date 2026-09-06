# La migración de MySis al ERP, desde el servidor

Qué pone **maryun01** en la carga de la historia de MySis al ERP nuevo, qué corre
dentro de la máquina y qué no, y cómo se vuelve atrás. Escrito el 5 de septiembre
de 2026.

**Esto no es el runbook.** El procedimiento paso a paso, las reglas de negocio y
las cifras de cada corrida viven en el repositorio del ERP, en
`maryun-erp/scripts/migracion/`: `README.md` explica qué se migra y por qué, y
`PUESTA-EN-MARCHA.md` cómo se corre. Los guiones de ahí son la **fuente única**;
este documento no los copia ni los resume, describe la máquina sobre la que
corren.

---

## 1 · Qué piezas del servidor participan

| pieza | papel en la migración |
|---|---|
| ClickHouse, base `dwh`, tablas `mysis_*` | el origen. Es el espejo de MySis, no MySis |
| `/srv/bin/ch-exec.sh` | la única forma sancionada de consultar ClickHouse sin sacar la contraseña |
| `maryun-erp-db` | PostgreSQL 18.6 con la base `maryun_erp`. El esquema `mig` vive **dentro** de ella |
| `10.8.0.1:5433` | ese mismo motor visto desde la VPN. Es por donde entra lo que no puede correr aquí |
| `~/mig/` (`/home/ialrringo/mig`) | la copia desplegada de los guiones. Ver la sección 4 |
| pgBackRest sobre `maryun_erp` | el punto de retorno. Ver la sección 7 |

La tubería, con la frontera marcada:

```
  dentro del servidor
  ┌──────────────────────────────────────────────────────────────────┐
  │  ClickHouse  dwh.mysis_*                                         │
  │        │  ch-exec.sh --format CSV                                │
  │        ▼                                                         │
  │     tubería  ──COPY FROM STDIN──►  esquema mig  (en maryun_erp)  │
  │                                          │                       │
  │                                    SQL de promoción              │
  │                                          ▼                       │
  │                                    tablas del ERP                │
  └──────────────────────────────────────────────────────────────────┘
                                             ▲
                       apertura de inventario │  (necesita Node)
                                             │
      máquina de escritorio con VPN ─────────┘   10.8.0.1:5433
```

`mig` son **17 tablas**: las 15 que recarga el aterrizaje, más `bitacora` y
`rechazo`, que son el registro de lo que hizo cada fase. Que vivan dentro de
`maryun_erp` y no en una base aparte tiene una consecuencia buena y una trampa: el
respaldo diario y el PITR las cubren sin hacer nada (sección 8), pero cualquier
volcado con `-n public` las deja fuera en silencio.

**La migración no toca MySis.** Lee el espejo que ya está en ClickHouse. La regla
de que en MySis sólo se lee (SERVIDOR.md §6) no se relaja por esto; lo que sí
importa es que el espejo esté fresco antes de aterrizar, y eso lo imprime el
propio `aterrizar.sh` al arrancar.

---

## 2 · Por qué el aterrizaje corre dentro del servidor

Antes lo hacía un pipeline de Mage. Un pipeline entra **desde fuera**, así que
necesita una cadena de conexión completa —usuario, contraseña, host— viviendo en
su configuración, y eso es una credencial de producción guardada en un sitio más.

Con ClickHouse y Postgres los dos en esta máquina, esa cadena deja de hacer
falta:

- `ch-exec.sh` lee la credencial de `/srv/secrets/clickhouse.env` y entra como
  `admin`. La contraseña no aparece en la línea de comandos ni en el historial,
  y conviene saber por qué se cumple: viaja por el entorno del contenedor
  —`docker exec -e CLICKHOUSE_PASSWORD`, que hereda el valor sin escribirlo— y
  **no** como `--password "$CLAVE"`. Esa forma, que es la que tenía el guion
  hasta el 5-sep-2026, sí la pone en el `argv` del proceso, donde la ve
  cualquiera que corra `ps -ef` en el momento justo. Si alguna vez hay que
  tocar `ch-exec.sh`, ése es el detalle que no se puede perder.
- Postgres se alcanza por `docker exec` contra `maryun-erp-db`, o sea por el
  socket local del contenedor: ahí no hay contraseña que pasar.

Ninguna credencial sale de la máquina ni pasa por `argv`. Es el mismo argumento
que ya sostiene al Postgres espejo: en [`postgres-espejo.md`](postgres-espejo.md)
se descartó `INSERT INTO FUNCTION postgresql(...)`, más corta, porque deja la
contraseña escrita en `system.query_log`.

Las dos formas rápidas, desde el servidor:

```bash
sudo /srv/bin/ch-exec.sh --format TSV --query "SELECT max(ingested_at) FROM dwh.mysis_mstr_pedidos"
```

```bash
sudo docker exec -i maryun-erp-db psql -U maryun -d maryun_erp -c "SELECT etiqueta, t, ventana_desde FROM mig.corte"
```

**El transporte es una tubería directa**, sin archivo intermedio, igual que el
del espejo. La diferencia está en el formato: aquí va CSV, y hay dos banderas que
tienen que ir juntas o el aterrizaje revienta —
`format_csv_null_representation=''` en ClickHouse y `NULL ''` en el `COPY` de
Postgres—. Por defecto ClickHouse escribe `\N` para nulo y una columna numérica
vacía llegaría como el texto `\N`. Está resuelto dentro de `aterrizar.sh`; se
apunta aquí porque es la clase de detalle que se pierde al reescribir el
transporte.

Y el recordatorio de siempre, que en esta tubería muerde: **`docker exec`
necesita `-i`** si le pasas algo por la entrada estándar. Sin `-i` el `COPY`
recibe un flujo vacío y no falla, simplemente no carga nada.

Última corrida medida: **2.007.602 filas en unos 12 segundos**, ventana de 48
meses (2022-09-05 → 2026-09-05), etiqueta `corte-2026-09-05`.

---

## 3 · El servidor no tiene Node

Es el hecho que parte el procedimiento en dos máquinas, y conviene saberlo antes
de abrir una sesión SSH con la intención de correr algo.

No hay `node` ni `npm` instalados, y no es un olvido: aquí todo lo que necesita
un tiempo de ejecución propio va en contenedor, por Coolify — que es exactamente
como corre el ERP. Nada que sea `.ts` o `.mjs` se ejecuta por SSH en esta
máquina.

Para la migración eso afecta a **una sola fase, y es la más cara**: la apertura de
inventario. No se puede traducir a SQL porque tiene que pasar por
`applyOpeningBalance` del servicio de costeo del ERP, el único punto autorizado a
escribir `avgCost` e `inventoryValue`. Escribir esas columnas a mano se saltaría
la única puerta que el ERP tiene para ellas.

Así que esa fase se corre **desde fuera**, con la VPN levantada, apuntando al
Postgres de producción por el mapeo de la VPN. El destino va en
`MIG_DATABASE_URL`, y **el valor nunca aparece en la línea de comandos**: un DSN
como argumento queda en el historial del shell, en la lista de procesos y en el
mensaje de error de cualquier herramienta que imprima el comando completo. Se
teclea en una petición que no hace eco, como manda `PUESTA-EN-MARCHA.md` §7.1:

```bash
read -rs -p 'MIG_DATABASE_URL: ' MIG_DATABASE_URL && export MIG_DATABASE_URL
```

```bash
npx tsx scripts/migracion/40-apertura.ts
```

Al terminar la fase, en esa misma sesión:

```bash
unset MIG_DATABASE_URL
```

Tres cosas sobre eso:

- **La VPN no es opcional.** El 5433 sólo existe en `10.8.0.1`; sin túnel no hay
  ruta.
- **El usuario tiene que poder escribir.** `dwh_lector` es de sólo lectura a
  propósito (SERVIDOR.md §6) y no sirve aquí. La credencial sale de
  `/srv/secrets/maryun-erp-db.env` y no se copia a ningún archivo del proyecto.
- Sin `--si`, `40-apertura.ts` **simula**: lee y no escribe. Lo que hace cada
  modo, y los rechazos que registra, está en el runbook del ERP.

Esperando en `mig` para esa fase: **25.172 pares bodega/SKU, 687.727 unidades,
$3.415.830.174**.

---

## 4 · Dónde viven los guiones, y qué le falta a eso

La fuente única es `maryun-erp/scripts/migracion/`. La copia que **se ejecuta**
está en `~/mig/` del usuario `ialrringo` — `aterrizar.sh`, `recargar.sh` y
`reponer-fk.sh` asumen esa ruta.

Eso tiene dos problemas que conviene tener escritos:

1. **Está fuera de `/srv`**, contra la regla de oro de la casa
   ([`estructura-srv.md`](estructura-srv.md)). No es desechable como
   `/var/lib/docker`, pero tampoco está donde vive lo precioso.
2. **No entra en el respaldo.** `backup/respaldo.sh` archiva
   `/srv/stacks/mage/project`, `/data/coolify`, `/srv/stacks/clickhouse/config.d`
   y `/srv/secrets` cifrado. Ningún directorio personal. Si se pierde el
   directorio, se pierde la copia desplegada.

La consecuencia práctica es menor —se reconstruye copiándola del repositorio del
ERP— pero hay que saber que se reconstruye así, porque no hay nada que lo haga
solo:

```bash
bash scripts/migracion/desplegar.sh
```

Ese guion copia sólo los `*.sh` y `*.sql` —no los `.md`, ni `40-apertura.ts` ni
`correr.mjs`, que el servidor no puede ejecutar— y después **compara las sumas
SHA-256 de las dos copias y falla si difieren**. Antes de que existiera, nada
comprobaba que el servidor estuviera corriendo la misma versión que uno acababa
de revisar: se editaba un `.sql` acá, no se volvía a copiar, y el servidor
seguía con el anterior sin decir una palabra. Para comprobar sin copiar,
`desplegar.sh --verificar`.

Lo limpio sería mover el directorio a `/srv/mig`, `2775 root:maryun`, y añadirlo
a la lista de `respaldo.sh`. Está en los pendientes de la sección 9.

---

## 5 · Cuándo se puede correr

La migración es tarea pesada contra ClickHouse y contra la base de producción, así
que va en la franja de siempre: **fuera de 11:00-22:30 UTC**, que es el horario
laboral de Chile (SERVIDOR.md §10).

Y no se solapa con los tres temporizadores que también pegan a producción:

| temporizador | cuándo |
|---|---|
| `maryun-respaldo` | 03:15 UTC |
| `maryun-preview` | 05:10 UTC |
| `maryun-espejo-postgres` | 07:30 UTC |

Los ligeros —la copia del PITR cada 15 minutos, los adjuntos cada hora— dan
igual.

**No despliegues el ERP mientras corre la recarga.** El entrypoint de la imagen
ejecuta `prisma migrate deploy` al arrancar (`docker/arrancar.sh`, ver
[`preview.md`](preview.md)), así que un push a `production` o un redespliegue en
Coolify significa migraciones de esquema entrando sobre una base que está siendo
reescrita en ese momento.

---

## 6 · Si algo queda a medias

Cada fase deja la base en un estado distinto, y no todos se cierran igual:

| se cortó en | qué queda tocado | cómo se cierra |
|---|---|---|
| el aterrizaje | sólo el esquema `mig`. `public` intacto | se relanza el guion entero |
| la recarga, antes del borrado | nada, o las FK ajenas sueltas | `reponer-fk.sh` |
| la recarga, entre el borrado y el final | el borrado ya está commiteado, y las FK sueltas | `reponer-fk.sh`, y seguir hacia adelante o volver con PITR |
| la apertura de inventario | es atómica por bodega: las cargadas quedan, la fallida no dejó nada | se repite; la salida fina es `41-revertir-apertura.sql` |

Dos matices que son del servidor y no del runbook:

**El aterrizaje no tiene red de seguridad.** `TRUNCATE` y `COPY` son dos
invocaciones de `psql`, o sea dos transacciones, así que un fallo a media tabla la
deja **vacía**. Es distinto de lo que hace el Postgres espejo, que envuelve cada
tabla en `BEGIN`/`TRUNCATE`/`COPY`/`COMMIT` y por eso un fallo no le vacía nada
([`postgres-espejo.md`](postgres-espejo.md)). Tampoco hay simulación ni recarga
por tabla suelta: se repite el guion completo.

**Las foreign keys sueltas.** La recarga suelta unas cuantas FK de módulos ajenos
antes de borrar y las repone al final, con validación. Si muere en medio, el
esquema queda a medias **en silencio** salvo por el aviso que imprime el propio
guion. Cerrarlo:

```bash
bash ~/mig/reponer-fk.sh
```

Es seguro correrlo siempre: si no hay nada suelto, no toca nada. Para ver si
quedó algo, desde el servidor:

```bash
sudo docker exec -i maryun-erp-db psql -U maryun -d maryun_erp -c "SELECT tabla, conname FROM mig.fk_soltada"
```

El porqué de soltarlas y reponerlas —y por qué reponer sin `NOT VALID` es la
garantía y no un detalle— está en el runbook del ERP.

---

## 7 · Volver atrás: pgBackRest

La recarga borra cientos de miles de filas de la base de producción, que es justo
la única base de esta máquina con recuperación a un instante. Antes de lanzar algo
destructivo se anota el punto de retorno:

```bash
sudo /srv/bin/pitr.sh info
```

Lo que imprime es hasta dónde se puede volver. La ventana de pérdida de
`maryun_erp` es de **un minuto** y la retención de **30 días**, local y en R2
([`pitr.md`](pitr.md) §3 y §4).

Comprobar que se puede restaurar a un instante, sin tocar producción:

```bash
sudo /srv/bin/pitr.sh ensayo "2026-09-05 01:32:40-04"
```

La hora es la de `America/Santiago` y conviene escribir el desfase explícito.

**Restaurar producción de verdad es el caso B de [`pitr.md`](pitr.md) §6**, y no
se copia aquí a propósito: es un procedimiento escrito y ensayado, y tenerlo en
dos sitios es tenerlo mal en uno de los dos.

Dos cosas que sí son propias de la migración:

- Como `mig` vive dentro de `maryun_erp`, una restauración devuelve **también** el
  aterrizaje al estado de ese instante. Si el corte era anterior, hay que volver a
  aterrizar antes de reintentar la promoción.
- Antes de llegar al PITR hay una salida más fina para la fase más cara:
  `41-revertir-apertura.sql` revierte sólo la apertura de inventario, con sus
  propias guardas. Empezar por ahí.

---

## 8 · Qué cubre el respaldo, y qué no

Del lado del servidor, `mig` está cubierto sin hacer nada:

| mecanismo | cubre `mig` | por qué |
|---|---|---|
| PITR (`pgbackrest`) | sí | el WAL es del cluster entero, no de un esquema |
| Respaldo diario 03:15 UTC | sí | `respaldo.sh` hace `pg_dump -Fc` de **la base completa**, sin `-n` |

La trampa está del otro lado. El comando de «respaldo antes de tocar» de
`ENTORNOS.md:227` lleva `-n public` a secas. Ese volcado **excluye `mig` sin
decirlo**, y es justo el que alguien correría antes de una operación destructiva.
Si se usa ése, no es un respaldo de la migración: el respaldo de la migración es
el de esta tabla. El de `MIGRACIONES.md:114` sí está bien escrito —lleva
`-n public -n mig` y explica ahí mismo por qué van los dos esquemas—: ése es el
que hay que copiar.

---

## 9 · Pendientes

- **Mover `~/mig/` a `/srv/mig`**, `2775 root:maryun`, y añadirlo a
  `backup/respaldo.sh`. Mientras siga en el directorio personal, está fuera de la
  regla de oro y fuera del respaldo.
- **Falta un guion de sincronización** `scripts/migracion/` → `~/mig/`, o al menos
  una comprobación de que las dos copias coinciden.
- **SERVIDOR.md §6 enseña a hablar con ClickHouse sin `ch-exec.sh`**: el ejemplo
  de «formas rápidas» entra como el usuario `default`, sin credencial, justo
  después de explicar que hay un usuario por función. Conviene reemplazarlo por
  `ch-exec.sh`, que es lo que se usa de verdad.
- **Este documento no está en la tabla de §13 de SERVIDOR.md.** Hay que añadirle
  la fila.
- **El pipeline `maryun_erp_migracion_mysis` sigue existiendo en Mage** y ya no es
  el camino. No se borra sin plan aprobado, como cualquier pipeline preexistente,
  pero conviene que nadie lo lance por error.
- **`maryun-erp/.env.produccion.local` todavía apunta al proyecto de Neon dado de
  baja.** Hay que reemplazarlo; ese archivo no debería seguir resolviendo a un
  destino muerto.
