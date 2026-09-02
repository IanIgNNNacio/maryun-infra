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

**El espejo es una instantánea.** `ventas_mysis` recibe filas durante el día:
entre dos mediciones de esta misma tarde pasó de 1.649.834 a 1.649.906. Para
cualquier comparación entre los dos motores hay que usar un período **cerrado**,
o las diferencias serán de calendario y no de datos.

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
