# El Postgres espejo de MySis

Montado el 2 de septiembre de 2026. **Los tableros de producción siguen en
ClickHouse.** Esto existe para lo que ClickHouse no puede hacer.

## Qué hay

| | |
|---|---|
| Contenedor | `dwh-postgres`, PostgreSQL 16.15 |
| Base | `dwh_espejo` |
| Puerto | `10.8.0.1:5434` — sólo por la VPN |
| Definición | `/srv/stacks/dwh-postgres/docker-compose.yml` |
| Credenciales | `/srv/secrets/dwh-postgres.env`, modo `0640 root:maryun` |
| Contenido | 36 tablas, 14.155.423 filas, 3.079 MB |

Dos esquemas, con papeles distintos a propósito:

- **`mysis`** — el espejo. Se **reescribe entero** cada noche. Nada que alguien
  escriba aquí sobrevive.
- **`manual`** — lo que se sube o se edita a mano. El refresco no lo toca.

Y dos usuarios:

- `dwh` — dueño. Lo usa el script de refresco.
- `bi_lector` — lo usan Metabase y Superset. **Sólo lee `mysis`**, y sólo puede
  crear tablas en `manual`. Comprobado: `has_table_privilege` sobre una tabla del
  espejo da `leer=true, escribir=false`.

Esa separación es la que hace que la subida de CSV desde Metabase sea segura: el
usuario de la conexión puede crear tablas, pero sólo donde no hay nada que
perder.

## Qué se copia y cómo

Las 35 tablas de MySis que viven en ClickHouse (`mysis_*` y `ventas_mysis*`),
más `periodos`, que no lleva el prefijo pero hace falta: la vista de ventas hace
`LEFT JOIN` contra ella.

Se excluye lo que termina en `_test`.

El transporte es una tubería directa:

```
ClickHouse  --FORMAT TabSeparated-->  tubería  --COPY FROM STDIN-->  Postgres
```

Los dos formatos coinciden en separador, en `\N` para nulo y en el escapado de
barra invertida, así que no hay conversión intermedia ni archivo temporal. La
alternativa era `INSERT INTO FUNCTION postgresql(...)` desde ClickHouse, más
corta, pero deja la contraseña de Postgres escrita en `system.query_log`.

Las tablas `ReplacingMergeTree` se leen con **`FINAL`**. Sin eso el espejo se
llevaría las versiones viejas de cada fila: en `ventas_mysis_2` son 34.788 filas
de diferencia.

Cada tabla se copia en **una sola transacción** —`BEGIN`, `TRUNCATE`, `COPY`,
`COMMIT`—, así que mientras corre los tableros siguen viendo la versión anterior
completa y un fallo a media copia no deja una tabla vacía. Eso ya se probó sin
querer: un refresco falló en 12 tablas por un punto y coma que faltaba, y los
datos quedaron intactos.

Se usa `TRUNCATE` y no `DROP` porque hay una vista encima: un `DROP` falla con
«cannot drop table because other objects depend on it». Sólo se recrea la tabla
cuando cambian sus columnas, y en ese caso `vistas.sql` devuelve las vistas.

## Refresco

`maryun-espejo-postgres.timer`, todos los días a las **07:30 UTC** —hora y media
después del pipeline de Mage que carga el DWH, para no copiar datos a medio
cargar—. Tarda unos 40 segundos. La bitácora va a `/var/log/maryun-espejo.log`.

A mano:

```bash
sudo /srv/bin/espejo-mysis-a-postgres.py --hazlo
sudo /srv/bin/espejo-mysis-a-postgres.py --hazlo --tabla ventas_mysis
sudo /srv/bin/espejo-mysis-a-postgres.py            # sólo lista, no copia
```

**El volcado completo es una instantánea.** `ventas_mysis` recibe filas durante
el día: entre dos mediciones de una misma tarde pasó de 1.649.834 a 1.649.906.
Para cualquier comparación entre los dos motores hay que usar un período
**cerrado**, o las diferencias serán de calendario y no de datos.

### Y encima, un incremental cada 15 minutos

Desde el 11-sep-2026 `mysis.ventas_mysis` ya no espera al volcado de la noche.
`maryun-espejo-ventas.timer` corre `/srv/bin/espejo-ventas-incremental.py
--hazlo` en el minuto 0, 15, 30 y 45 de cada hora, y tarda **0,8 segundos**.

Por qué un guion aparte en vez de correr el volcado completo cada cuarto de
hora, que habría sido una línea: porque el volcado copia la tabla entera con
`TRUNCATE` + `COPY`, y ese `TRUNCATE` toma `ACCESS EXCLUSIVE` durante los diez
segundos que dura. En horario laboral eso es un tablero congelado cada quince
minutos. El incremental sólo inserta.

Cómo decide qué traer:

1. marca de agua = `max(ingested_at)` del espejo
2. ClickHouse devuelve las filas con `ingested_at >= marca`
3. van a una tabla de paso `UNLOGGED`
4. se insertan las que no estén ya, comparando por `(pid, sku)`

El `>=` no es un descuido: `ingested_at` tiene resolución de segundo y un lote
puede repartirse entre dos corridas, así que con `>` se perderían las filas que
compartan segundo con la última traída. Repetirlas no cuesta nada porque el
anti-join las descarta. Medido en la primera corrida real: 1.128 filas traídas,
1.079 insertadas, 49 ya estaban.

**El volcado de las 07:30 sigue siendo el que manda**, y no sobra. El
incremental no borra ni actualiza: si alguien borra filas en ClickHouse —pasó,
cuatro mutaciones `DELETE` el 2026-09-03— aquí quedarían de fantasma hasta el
volcado. Y el exportador de Mage tampoco actualiza: una vez cargada una línea,
sus columnas `deuda`, `pmp`, `factura` y `entregado` quedan fijas para siempre.
**Para cobranza no se usa este espejo**: el saldo vivo está en `Receivable` del
ERP.

```bash
sudo /srv/bin/espejo-ventas-incremental.py            # dice qué traería
sudo /srv/bin/espejo-ventas-incremental.py --hazlo
sudo systemctl list-timers maryun-espejo-ventas.timer
tail -n 40 /var/log/maryun-espejo-ventas.log
```

La tabla llegaba **sin ningún índice** —el volcado es `TRUNCATE` + `COPY` y
nadie los había necesitado—. Ahora tiene tres, creados desde `vistas.sql`:
`(pid, sku)` para el anti-join, `ingested_at` para la marca de agua (leerla sin
índice costaba 105 ms de `Parallel Seq Scan` sobre 111.752 bloques) y
`facturado` para el filtro de fecha de los tableros. `(pid, sku)` va **sin
`UNIQUE`** a propósito, aunque hoy los 1.654.102 pares sean distintos: un
duplicado que se colara en ClickHouse abortaría el volcado nocturno entero y
dejaría el espejo congelado sin que se note.

## Una sola base de reportes, y por qué no son tres

Es fácil contar tres bases y no son tres:

| | qué es | para qué |
|---|---|---|
| `maryun-erp-db` `:5433` | la base del ERP | **operar**. Es donde la aplicación escribe |
| `maryun-erp-replica` `:5436` | **la misma base**, puerta de sólo lectura | que una consulta pesada no toque la que factura |
| `dwh-postgres` `:5434` | el segundo Postgres | **la única base de reportes** |

La réplica **no es otra base de datos**: es un *hot standby* físico, los mismos
bytes, a 0 de retraso. No hay dato ahí que no esté en producción.

Así que fuentes de reporte hay **una**: `dwh-postgres`, y dentro de ella una
vista, `global.ventas`. La columna `origen` decide qué mira cada tablero:

| tablero | consulta |
|---|---|
| los «global» | `global.ventas` entera |
| los «ERP» | `global.ventas WHERE origen = 'ERP'` |
| las pantallas que ya existían | su propia base, por Prisma. Un ERP lee su base |

**El filtro por `origen` no cuesta nada**, y eso no es una suposición: Postgres
propaga la condición a cada rama del `UNION ALL`, ve que `'MYSIS' = 'ERP'` es
falso y **poda la rama de MySis entera**. El plan medido de un tablero «ERP» ni
siquiera menciona `ventas_mysis`: 30 ms, 5 bloques, todo empujado por FDW al
ERP. No se leen 1,65 millones de filas para descartarlas.

Metabase necesita entonces **una sola conexión** para los seis tableros nuevos:
«Postgres espejo MySis», cuyo filtro de esquemas pasó a `mysis,manual,global`.

## La capa `global`: MySis y el ERP en la misma vista

`global.ventas` junta, en el grano de **una línea de venta**, las dos mitades
del negocio:

| | de dónde | cuántas líneas | frescura |
|---|---|---|---|
| `origen = 'MYSIS'` | `mysis.ventas_mysis` | 1.655.181, desde 2018-05-17 | 15 min |
| `origen = 'ERP'` | la réplica del ERP, por `postgres_fdw` | **0 hoy** | en vivo |

Que la mitad del ERP esté vacía es correcto, no un fallo: las ventas de
producción son migradas —`legacyRef` no nulo en el 100%— y **ninguna nació en
el ERP**. La vista se llenará sola con el primer despacho.

**`legacyRef IS NULL` es el criterio de origen**, y es sólido: ningún camino del
ERP escribe esa columna (el `create` de `sale-service.ts` enumera campos uno por
uno, `createSaleSchema` no la lista, y `CreateSaleInput` no la declara), y
ninguno de los cuatro `UPDATE` que existen sobre `Sale` la toca. El índice único
que ya tenía sirve para el `IS NULL`: **0,035 ms**.

**Por qué FDW y no un ETL de las ventas del ERP.** No hay nada que agendar ni
desfase que explicar; pero sobre todo, `Sale` **no tiene `createdAt` ni
`updatedAt`** —28 columnas, ninguna de auditoría—, así que un incremental por
marca de agua del lado del ERP no tendría de dónde agarrarse.

**Los cuatro enum se recrean aquí.** `SaleStatus`, `SaleDocType`, `Origin` y
`StockMoveType` existen como tipos en `dwh_espejo` con las mismas etiquetas. El
primer intento fue declararlos `text` en las tablas foráneas, y falla: como
`postgres_fdw` empuja el `WHERE` al servidor remoto, allí la columna sigue
siendo del enum y `status <> ALL ('{DRAFT,CANCELLED}'::text[])` revienta con
*«operator does not exist: public."SaleStatus" <> text»*. El cast explícito
tampoco salva, porque siendo `text`→`text` en local se borra antes de
deparsear. El precio de la solución: si Prisma añade una etiqueta, hay que
añadirla aquí o la lectura de esa fila fallará.

**El costo de la mitad del ERP sale de `StockMovement` vía `Delivery`**, igual
que en `domain/reports/sales-fact.ts`, y no de `SaleLine.histUnitCost`: esa
columna es del importador y en una venta nativa siempre será `NULL`. Usarla
daría costo 0 y margen 100%, que es justamente el fallo que hoy tiene
`/reportes/ventas` en el ERP.

**Red de seguridad.** El rol `erp_lector`, que es el que viaja por el FDW, lleva
`statement_timeout = 120s`. La réplica tiene `max_standby_streaming_delay = 30s`
y `hot_standby_feedback = on`: una consulta larga contra la réplica hace que el
**primario** retenga tuplas muertas mientras corra, así que conviene que no
pueda correr para siempre.

### La historia de MySis estaba rota antes de 2022, y se arregló

Al construir el tablero del nivel 3 salió a la luz: `dwh.ventas_mysis` tenía
sólo **27.225 líneas** entre 2018 y 2021, y no eran pocas sino **equivocadas** —
2018 y 2019 con venta cero en todas sus filas, 2020 y 2021 con venta
**negativa** ($-88 MM y $-803 MM). Eran notas de crédito y ajustes sueltos, sin
las ventas que los originaron.

La historia buena estaba al lado, en `dwh.ventas_mysis_prueba`. El 12-sep-2026
se trasplantó:

1. copia de seguridad de las 27.225 filas en `dwh.respaldo_ventas_pre2022_2026_09_12`
2. `DROP PARTITION` de las 43 particiones anteriores a 2022 —metadato,
   instantáneo, sin mutación
3. `INSERT ... LIMIT 1 BY pid, sku` desde `ventas_mysis_prueba`, que de paso
   quitó los 2.682 duplicados que esa tabla arrastraba

De 2022 en adelante no se tocó nada: ahí las dos tablas ya coincidían hasta en
el detalle, y `ventas_mysis` es la que está fresca.

| | antes | después |
|---|---|---|
| líneas | 1.656.013 | **2.610.413** |
| desde | 2018-05-17, vacío hasta 2022 | **2018-05-16, con importes** |
| venta total | $62.050 MM | **$100.418 MM** |

Por año, lo recuperado: 2018 $4.174 MM · 2019 $5.212 MM · 2020 $15.229 MM ·
2021 $12.783 MM.

**El incremental no habría traído esto nunca**, y conviene entender por qué: va
por `ingested_at`, y estas filas llevan la marca de cuando se cargaron en la
tabla de pruebas, anterior a la marca de agua del espejo. Después de una
operación así hay que correr el volcado completo a mano. Es el mismo motivo por
el que el volcado nocturno no se puede quitar.

## Los tres niveles de tablero

Desde el 12-sep-2026 hay tres tableros de ventas v3 en Metabase, y la diferencia
entre ellos es **qué mitad del negocio miran**:

| nivel | tablero | colección | conexión | qué muestra | hoy |
|---|---|---|---|---|---|
| 1 | `Ventas v3` (id 26) | 30 | 6 · réplica | sólo lo **nacido en el ERP** | $0 |
| 2 | `Ventas v3 ERP` (id 27) | 31 | 6 · réplica | **toda la base del ERP** | $9.894 MM · 82.364 doc |
| 3 | `Ventas v3 global` (id 28) | 32 | 5 · espejo | **toda la historia** | $100.418 MM · 929.344 doc |

Los niveles 1 y 2 comparten SQL: el 1 es el 2 con `AND "Sale"."legacyRef" IS
NULL`. El nivel 3 es otra cosa —13 consultas reescritas contra `global.ventas`,
**sin un solo JOIN**, porque la vista ya viene plana.

Que el nivel 1 marque cero es correcto, no un fallo: todavía nadie despacha
desde el ERP.

**Las cifras de los niveles 1 y 2 miden `SaleLine.total`**, o sea venta neta de
línea. No cuadran con `SUM(Sale.total)` —$11.774 MM— porque ése es el total del
documento con IVA. Son dos medidas distintas y las dos correctas.

**El «N° documentos» del nivel 2 da 82.364 y no 82.365.** La venta `H-V-1128915`
del 2026-02-17 no tiene ninguna línea, y el tablero se arma desde `SaleLine`. No
se pierde plata; si algún día se le pone línea o se limpia, el contador sube
solo.

### Lo que el nivel 3 todavía no hace

**No restringe por alcance.** Los dos parámetros bloqueados del embebido
—`alcance_sucursales` y `alcance_vendedor`— viajan con **ids** de sucursal y de
vendedor del ERP, y `global.ventas` sólo tiene **nombres**. Los tags quedan
declarados para que el JWT no falle, pero no filtran. **No embeber el nivel 3
para usuarios con alcance limitado** hasta que la vista exponga los ids o el ERP
mande nombres.

### ¿Hace falta materializarla? Todavía no

Medido el 11-sep-2026 sobre las 1.655.181 líneas, tiempos en caliente:

| tile | tiempo |
|---|---|
| venta y margen por mes, 24 meses | 567 ms |
| venta por sucursal, todo el histórico | 993 ms |
| venta y margen por familia | 1.041 ms |
| top 100 clientes | 1.074 ms |
| recuento por origen, historia completa | 905 ms |

Al recuperar la historia de 2018-2021 la tabla creció un 58%, y ese último
barrido completo se fue de 905 ms a **1.933 ms**: justo en el umbral.

Un tile de historia completa cuesta **alrededor de un segundo**; con filtro de
fecha, la mitad. El umbral acordado para materializar es **dos segundos por
tile**, y no se alcanza. Cuando se alcance —crecimiento del histórico, o
decenas de miles de ventas nativas encareciendo el lado del FDW— el camino es
`REFRESH MATERIALIZED VIEW CONCURRENTLY` enganchado detrás del incremental, que
exige antes un índice único sobre la vista. **Medir primero.**

Todo esto vive en `/srv/stacks/dwh-postgres/vistas.sql`, que el volcado nocturno
reaplica entero al terminar. Es el único sitio donde ponerlo: si alguna vez
cambian las columnas de `ventas_mysis`, el volcado hace `DROP TABLE ... CASCADE`
y se lleva la vista por delante; `vistas.sql` la devuelve.

## Que el espejo es fiel: la prueba

Sobre el año 2024 completo, los dos motores dan lo mismo hasta el centavo:

| | ClickHouse | Postgres |
|---|---|---|
| filas | 347.163 | 347.163 |
| ventas | 13.810.288.980,21 | 13.810.288.980,21 |
| unidades | 3.993.010 | 3.993.010 |
| clientes distintos | 23.147 | 23.147 |
| margen | 3.399.602.922,87 | 3.399.602.922,87 |

## Velocidad: dos números, y hay que no confundirlos

Medido en el servidor, sin pasar por Metabase, cuatro corridas y mediana de las
tres últimas.

**Sobre la tabla base**, donde las dos consultas son idénticas. Esto compara
motores:

| consulta | ClickHouse | Postgres | |
|---|---|---|---|
| Suma sobre todas las filas | 0,076 s | 0,155 s | 2,03x |
| Suma con filtro de un año | 0,076 s | 0,137 s | 1,80x |
| Agrupado por sucursal | 0,078 s | 0,142 s | 1,83x |
| Serie mensual completa | 0,081 s | 0,235 s | 2,89x |
| Clientes distintos | 0,081 s | 0,190 s | 2,35x |
| Cruce con la maestra de SKU | 0,083 s | 0,156 s | 1,88x |
| Top 50 por importe | 0,079 s | 0,142 s | 1,81x |

ClickHouse gana siempre, entre 1,8x y 2,9x, y llama la atención lo **plano** que
es: 0,08 segundos haga lo que haga.

**Sobre la vista**, que es lo que consultan los tableros de verdad:

| consulta | ClickHouse | Postgres | |
|---|---|---|---|
| Suma sobre todas las filas | 0,569 s | 0,196 s | 0,35x |
| Serie mensual completa | 0,581 s | 0,281 s | 0,48x |
| Agrupado por sucursal | 0,205 s | 0,154 s | 0,75x |
| Top 50 por importe | 0,197 s | 0,150 s | 0,76x |

Aquí gana Postgres. **Y no es porque Postgres sea mejor:** es que la vista de
ClickHouse le cuesta 7 veces más que su propia tabla base —0,57 s contra
0,08 s—, y eso se lo hace ella misma. Ver la sección siguiente.

## El hallazgo que vale más que todo lo anterior

`dwh.vw_ventas_mysis_periodos` está escrita así:

```sql
SELECT v.*, ..., any(p.start_date) AS start_date, any(p.end_date) AS end_date
FROM dwh.ventas_mysis AS v
LEFT JOIN dwh.periodos AS p ON v.periodo = CAST(p.periodo, 'String')
GROUP BY v.*, ...
```

Dos problemas, y los dos importan:

**1. El join no casa nunca.** `ventas_mysis.periodo` guarda `'2018-05'` y
`periodos.periodo` guarda `201805`. Comparar `'2018-05'` con `'201805'` no da
nunca. Y como en ClickHouse esas columnas son `Date` y no `Nullable(Date)`, el
`LEFT JOIN` sin pareja no deja nulo: rellena con la fecha cero. **Las
1.649.906 filas tienen `start_date = 1970-01-01`.** Eso es peor que un nulo,
porque parece un dato y un filtro por fecha lo acepta.

Se comprobó que con el formato correcto casarían **todas**:

```sql
-- en Postgres, para medirlo
JOIN mysis.periodos p
  ON to_char(to_date(p.periodo::text, 'YYYYMM'), 'YYYY-MM') = v.periodo
-- resultado: 1.649.834 filas casan, o sea el 100 %
```

**2. El `GROUP BY v.*` cuesta 7x.** Está ahí para que `any()` se quede con una
fila del lado derecho. No hace falta: se comprobó que `dwh.periodos` tiene 100
filas y 100 períodos distintos, o sea que la clave es única y un `LEFT JOIN`
normal no multiplica nada. Quitar el `GROUP BY` devolvería la vista a los
0,08 segundos de la tabla base.

**Arreglar esas dos cosas en ClickHouse mejora los tableros de producción y no
requiere mover nada.** Es más valioso que cualquier decisión sobre Postgres.

Mientras no se arregle, el espejo **replica el fallo a propósito** —hay un
`coalesce` a `1970-01-01` en `vistas.sql`— para que las comparaciones entre los
dos motores no salgan distintas por el motivo equivocado.

## Qué poner en cada uno

Del informe [postgres-vs-clickhouse-en-bi.md](postgres-vs-clickhouse-en-bi.md),
que verificó 13 diferencias y descartó 11.

**En Postgres:**

1. La subida de CSV y Excel. En Metabase sólo funciona en Postgres, MySQL,
   Snowflake y Redshift; con ClickHouse **sólo en ClickHouse Cloud**, y en el
   autoalojado la función no aparece. Ya está apuntada al esquema `manual`.
2. Las Actions de Metabase —escribir desde un tablero—. El driver de ClickHouse
   declara `:actions false`. Sirve para un formulario que corrija un precio o
   marque una factura como revisada.
3. Las tablas maestras que se editan a mano: listas de precios, mapeos de SKU y
   de cuentas, metas de venta.
4. SQL Lab cuando importe la validación de sintaxis en vivo o la estimación de
   costo: los dos sólo existen para Presto y Postgres.
5. El botón «Stop» de Superset. En Postgres ejecuta `pg_terminate_backend()`; en
   ClickHouse no hay `cancel_query`, así que la consulta se marca detenida en la
   pantalla y **sigue consumiendo el servidor**.

**En ClickHouse:** todo lo demás, y en particular los tableros. Las carencias de
arriba son de capa BI, no de lectura analítica, que es lo que hacen los
tableros.

Ojo con una cosa que ya es así y conviene saber: el **metastore de Superset** no
puede ser ClickHouse —sólo admite PostgreSQL o MySQL— y ya es Postgres, en
`superset-db`. Son dos motores que respaldar, y el chico es el crítico: si se
pierde, se pierden los tableros aunque ClickHouse esté intacto.

## Dónde verlo

- Metabase, base **«Postgres espejo MySis»** (`db=5`), 35 tablas visibles.
- Superset, conexión **«Postgres espejo MySis»**, con subida de archivos, CTAS y
  CVAS habilitados y forzados al esquema `manual`. `allow_dml` queda en `false`.
- Tablero de comparación: **`/dashboard/25`**, «Postgres espejo vs ClickHouse».
  Seis métricas, cada una en los dos motores, ClickHouse a la izquierda.

En ese tablero es normal ver diferencias pequeñas en las métricas de los últimos
12 meses: el espejo se refresca una vez al día y el origen recibe filas durante
el día. La prueba de fidelidad es la del período cerrado, más arriba.

## Herramientas

```bash
sudo /srv/bin/espejo-mysis-a-postgres.py --hazlo   # refrescar
sudo /srv/bin/medir-motores.py                     # medir los dos motores
sudo /srv/bin/mb-registrar-postgres.py --hazlo     # registrar en Metabase
sudo /srv/bin/mb-tablero-comparacion.py --hazlo    # crear el tablero
sudo /srv/bin/mb-comparar.py                       # contrastar por API
```

Las tres últimas leen la clave de API de Metabase por la entrada estándar, no
por argumento, para que no aparezca en `ps` ni en el historial.

---

## Apéndice · filtros del tablero 24 (Facturas RCV)

Se añadieron dos filtros —**Periodo** y **Proveedor (RUT)**— al tablero de
Facturas RCV, que no tenía ninguno. Las 16 tarjetas son SQL nativo, así que un
filtro de tablero no se conecta solo: hay que meter una cláusula opcional
`[[AND …]]` en cada consulta, y esas cláusulas desaparecen cuando el filtro está
vacío.

**Resultado:** 14 tarjetas con filtro, **0 rotas**, 11 cambian su resultado al
filtrar. Las tres que no cambian el número de filas lo hacen por motivos
legítimos: una tiene dos estados fijos, otra es un top 20 y la tercera topa en
`LIMIT 500` con y sin filtro.

**Dos quedaron fuera a propósito**, y siguen funcionando sin filtro:

- **363, «Cobertura de clasificación por dimensión»** — al insertar la cláusula
  responde `syntax error at or near "AND"`. Su consulta exterior no tiene un
  único punto donde colgarla.
- **361, «Pivot cuenta contable × mes»** — es tabla dinámica: Metabase envuelve
  el SQL para pivotar y la cláusula rompe ese envoltorio.

Para que las acepten hay que editarlas a mano mirando su SQL entero. Están en la
lista `NO_TOCAR` de `bin/mb-filtros-rcv.py` para que nadie lo reintente a ciegas.

**El original de las 16 está guardado** en `/srv/secrets/tablero24-originales.json`,
y `bin/probar-filtros-rcv.py` comprueba que ninguna quedó rota y cuáles filtran
de verdad.

Una lección de esto: la primera comprobación solo probó las tarjetas **sin**
filtro puesto, y las dos que fallaban pasaron. Un filtro hay que probarlo
aplicado.
