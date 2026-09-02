# Postgres espejo vs ClickHouse: qué mover y qué dejar quieto

Alcance: Metabase OSS v0.63 y Superset 6.1.0. Todo lo que sigue está verificado en documentación oficial y código de esas versiones, salvo donde digo lo contrario.

---

## 1. Lo que gana de verdad con Postgres

Ordenado por impacto en un ERP.

**1.1 El metastore de Superset. Esto no es opcional.**
ClickHouse no puede ser la base de metadatos de Superset. Los motores soportados para `SQLALCHEMY_DATABASE_URI` son PostgreSQL 10–16 y MySQL 5.7/8.X. SQLite está descrito como "highly discouraged" en producción. Ahí viven los tableros, usuarios, permisos, RLS, alertas y saved queries. Gratis, sin licencia.

Consecuencia directa para el VPS: son **dos motores que respaldar, no uno**. Y el chico es el crítico. Si se pierde el Postgres del metastore, se pierden los tableros aunque ClickHouse esté intacto.

**1.2 Writeback desde el tablero (Actions de Metabase).**
`Actions` solo funciona en PostgreSQL y MySQL. En ClickHouse el driver declara `:actions false`. No es tema de licencia: es del motor. Gratis en OSS.

Esto habilita lo que hoy no se puede: un formulario en un dashboard para corregir un precio, marcar una factura como revisada, ajustar un stock. Límites reales: opera sobre models del query builder, una sola tabla, una sola PK.

**1.3 Subir CSV/Excel desde la UI.**
- Metabase: uploads soporta PostgreSQL, MySQL, Snowflake, Redshift y "ClickHouse (only supported on ClickHouse Cloud)". En **ClickHouse autoalojado la función no está en versión reducida: no aparece**. El gate está en el código, no solo en la doc (`database-supports?` exige que el servidor se identifique como Cloud). Gratis en OSS.
- Superset: `supports_file_upload = False` en ClickHouse, en ambos drivers. Y no es cosmético: `UploadCommand.validate()` lanza `DatabaseUploadNotSupported()` aunque un admin marque `allow_file_upload=True` por API. Postgres sí, pero hay que habilitarlo por conexión y fijar los schemas permitidos en Extra.

Casos de uso concretos: listas de precios, mapeos de SKU y de cuentas, metas y presupuestos de venta, cuadres manuales de stock.

**1.4 Cancelar una consulta en el motor (botón Stop de Superset).**
En Postgres, Superset ejecuta `pg_terminate_backend()`. En ClickHouse no hay `cancel_query` ni `get_cancel_query_id`, así que el Stop marca la consulta como STOPPED en Superset y deja de hacer polling, pero **la consulta sigue quemando el clúster**. Con scans de kardex o RCV esto deja trabajo huérfano y mata la concurrencia del VPS.

**1.5 Validación de SQL en vivo en SQL Lab.**
Solo existen validadores para Presto y PostgreSQL (`SQL_VALIDATORS_BY_ENGINE`). En ClickHouse el analista se entera del error de sintaxis cuando la consulta ya salió.

**1.6 Estimación de costo antes de ejecutar.**
Soportado en Presto, Postgres y BigQuery. ClickHouse no. Nota: el flag `ESTIMATE_QUERY_COST` viene en `False` por defecto, hay que activarlo.

**1.7 Lo que NO gana, aunque suene a que sí (todo de PAGO):**
- **Editable tables** (editar filas desde la UI): Pro/Enterprise, y solo PostgreSQL/MySQL. En OSS no existe en ningún motor. La idea de "corregir la maestra de productos desde Metabase" hay que sacarla del alcance; eso vive en el ERP.
- **Database routing** (una conexión por cliente/empresa): Pro/Enterprise, y ClickHouse figura excluido por nombre junto a Oracle, Spark SQL y Vertica. El issue está cerrado como "not planned" por razones arquitectónicas. Si el plan era multi-tenant por conexión, no se resuelve así.
- **Persistencia por modelo individual**: Pro/Enterprise. En OSS la persistencia se activa por base de datos completa (PostgreSQL, MySQL, Redshift), no modelo por modelo. Para ClickHouse es doblemente irrelevante: no está soportado. Además la propia doc la marca como función en camino a deprecación, así que no vale construir arquitectura encima.

---

## 2. Por qué no conviene mover los tableros a Postgres

Nada de lo de arriba es un argumento para mover los tableros. Todas las carencias de ClickHouse son de **capa BI**, no de capa de datos: uploads, formularios, validación, estimación, cancelación. Ninguna es de lectura analítica, que es lo que hacen los tableros.

Lo que ClickHouse sigue haciendo mejor y ya está montado:

- **Lectura directa de archivos** sin cargarlos: `url()`, `s3()`, `file()` con autodetección de formato. Sirve para explorar extractos del SII, RCV y cartolas. Postgres en core solo tiene `file_fdw` + `COPY FROM PROGRAM`: sin HTTP(S), sin Parquet, sin JSON nativo, y exige superusuario o los roles `pg_read_server_files` / `pg_execute_server_program`. La paridad requiere extensiones fuera del core.
  Advertencias: `file()` en clickhouse-server está restringido a `user_files_path`, y no está soportado en ClickHouse Cloud (irrelevante para el VPS).
- **Projections**: aceleran un tablero sin tocar ninguna pregunta guardada. El optimizador elige la projection con menos datos que escanear, sin modificar la consulta del usuario. Cubre agregación y proyección sobre una tabla; no admite JOIN ni WHERE en la definición, no encadena, solo MergeTree.
- **Ingesta streaming**: engine Kafka + materialized views, con async inserts que hacen flush cada 200 ms por defecto. Está en OSS, sin costo.

Mover los tableros significaría reconstruirlos, perder projections y meter el volumen de facturas y kardex en un motor de filas para ganar funciones que se resuelven con un Postgres chico al lado.

---

## 3. Otros motores: qué es ruido

**DuckDB: ruido.** Su gracia es leer Parquet/CSV/JSON por disco o HTTPS sin cargarlos. ClickHouse ya lo hace con `url()`/`s3()`/`file()`. Suma cero al stack. Además en Metabase OSS el driver es de comunidad, no oficial.

**StarRocks: ruido a esta escala.** La reescritura transparente de consultas sobre vistas materializadas asíncronas es real y técnicamente lo más elegante de la lista: acelera un tablero sin tocar la pregunta. Pero ClickHouse cubre la parte de una tabla con projections, que también se eligen solas. Lo que StarRocks agrega de verdad es JOIN, multi-tabla, union y MV anidadas. Costo: un motor más en el VPS, y en Metabase solo hay driver de comunidad. Detalle a favor de ClickHouse: en StarRocks el rewrite viene con `query_rewrite_consistency=CHECKED`, o sea que si la MV está rancia cae de vuelta a las tablas base y la aceleración no está garantizada.

**Apache Druid: no.** ClickHouse ya cubre streaming. Y el soporte está peor de lo que parece: en Superset 6.1.0 el conector nativo fue eliminado (SIP-11/SIP-68) y queda solo vía `pydruid`, que no viene empaquetado; en Metabase el driver pre-JDBC está deprecado.

**Trino: solo si aparece una necesidad concreta.** Da federación real, un catálogo por fuente y JOIN entre ClickHouse y Postgres en la misma consulta. Pero hay caminos más baratos: ClickHouse puede montar el Postgres como `CREATE DATABASE ... ENGINE = PostgreSQL(...)` con acceso en vivo a la lista de tablas, y existe `pg_clickhouse` (FDW Apache 2.0) para el sentido inverso. Ojo con el piso de versión: el conector ClickHouse de Trino exige ClickHouse 25.3 o superior. Dato relevante: Metabase OSS v0.63 sigue sin joins entre bases de datos.

**TimescaleDB: el único de bajo riesgo, pero sin beneficio confirmado.** El costo de driver es cero: `psycopg2` viene incluido en Superset, y Metabase lo conecta con el driver PostgreSQL. Dos correcciones a "costo cero":
- Superset no lo ve como Postgres normal: trae `TimescaleDBEngineSpec(PostgresBaseEngineSpec)`, así que aparece como entrada separada en el selector. Es una subclase delgada, el costo de instalación sigue siendo cero.
- En Metabase hay un paso manual: el driver de Postgres solo excluye `information_schema` y `pg_catalog`, no los esquemas internos de Timescale. Al sincronizar recorre `_timescaledb_internal` y una tabla por chunk, así que el problema escala con el histórico. Hay que restringir a mano los esquemas sincronizados.

No pongo aquí un beneficio verificado de Timescale sobre lo que ya tienen. Las dos funciones que se le suelen atribuir (continuous aggregates y `time_bucket_gapfill`) no pasaron verificación como ventajas comparativas, y son de Community Edition bajo licencia TSL: gratis para autohospedaje, no en la edición Apache-2 ni en la mayoría de los Postgres gestionados. Si se prueba, que sea con una hipótesis medible, no por defecto.

---

## 4. Recomendación concreta

**En el Postgres espejo:**
1. Metastore de Superset. Obligatorio, no negociable.
2. "Upload database" de Metabase (Admin > Settings > Uploads pide BD y schema con permiso de escritura). Puede ser el mismo Postgres, en un schema aparte del metastore.
3. Destino de `allow_file_upload` en Superset: habilitarlo en la conexión y fijar los schemas permitidos en Extra.
4. Tablas maestras y puente que se editan a mano: listas de precios, mapeos de SKU y de cuentas, metas y presupuestos de venta, tablas de conversión, cuadres.
5. Todo el writeback, vía Actions de Metabase. Solo aquí funciona.
6. SQL Lab exploratorio cuando importe la validación en vivo y la estimación de costo.
7. **No poner ahí** los tableros de volumen: ventas, facturas, kardex, stock.

**En ClickHouse:**
1. Sigue siendo el motor de los tableros. Estrictamente solo lectura analítica desde el BI.
2. Compensar por el lado del motor lo que Superset no le da: `max_execution_time`, `max_rows_to_read` y `max_bytes_to_read` por perfil de usuario, más quotas. `KILL QUERY` a mano. Existe `cancel_http_readonly_queries_on_client_close`, pero no se gatilla con el botón Stop de Superset: es mitigación del lado servidor, no la feature de Superset.
3. Ingesta de maestros por fuera del BI: `clickhouse-client`, `INSERT` desde el pipeline de Mage, o `url()`/`s3()`.
4. Projections para los tableros lentos de una sola tabla, si aparecen.
5. Multi-tenant: filtro por columna de tenant más RLS de Superset. No por database routing (es de pago y ClickHouse está excluido).

**Cruces entre los dos:** hacerlos por pipeline, no dentro de Metabase. Metabase OSS v0.63 no hace joins entre bases de datos.

**Respaldos del VPS dedicado:** dos políticas, no una. El Postgres es chico pero es punto único de falla del BI completo.

---

## Mitos que conviene no repetir

Estos aparecen en listados de diferencias y **no se sostienen**. No los usen para justificar decisiones.

- **"ClickHouse no soporta report timezone en Metabase."** Falso. La lista de la doc de localización está obsoleta. En el tag v0.63.16 el driver declara `:set-timezone true`, lo implementa de verdad (`session_timezone` como server setting) y la cadena cierra hasta `report-timezone-id-if-supported`. Lo que sí está en `false` es `:convert-timezone`, que es otra función: la expresión `convertTimezone` del query builder.
- **"Superset no puede hacer CTAS contra ClickHouse."** Refutado. Superset emite un `CREATE TABLE ... AS SELECT` sin ENGINE, pero ClickHouse ya trae `default_table_engine = MergeTree` por defecto desde 24.4 y `create_table_empty_primary_key_by_default = true` desde 25.12 (así viene en 26.8 LTS). Sobre un ClickHouse estándar actual funciona sin configurar nada. Aviso: verificado por inspección de código y defaults, no ejecutado contra la instancia; y si el destino fuera anterior a 25.12, sí habría que configurarlo.
- **"`allow_dml` no funciona igual en ClickHouse."** Refutado. El gate está en `sql_lab.py` y es agnóstico al motor; el spec de ClickHouse no lo sobrescribe. El SQL viaja crudo al cursor DBAPI, así que las carencias ORM del dialecto no aplican a SQL Lab. Nota aparte, no una carencia: commit/rollback son no-ops en ClickHouse.
- **"El RLS de Superset es parcial en ClickHouse."** Refutado. `get_sqla_row_level_filters()` no tiene ninguna ramificación por dialecto: la cláusula entra como texto crudo. El admin escribe la cláusula en el dialecto de su motor. No lo probé contra una instancia viva; la prueba es de cinco minutos (crear un filtro RLS y revisar "View query").
- **"Druid da streaming sub-segundo y ClickHouse solo parcialmente."** Refutado. ClickHouse OSS tiene Kafka engine, y con async inserts el flush por defecto es de 200 ms.
- **"DuckDB permite consultar Postgres desde otro motor y eso es una ventaja nueva."** Refutado, y con la polaridad invertida. Postgres lo hace nativo con `postgres_fdw` (contrib del core), y ClickHouse con `ENGINE = PostgreSQL(...)` o la table function `postgresql()`. No existe extensión core de ClickHouse en DuckDB: lo único es `chsql_native`, community y declarada experimental.

## Lo que quedó sin verificar

- **Transforms de Metabase sobre ClickHouse autoalojado.** Hay contradicción: la doc de v0.63 dice "ClickHouse (only ClickHouse Cloud)", el flag del driver dice `:transforms/table true`. No lo resolví. Es justo la vía que suele proponerse para reemplazar la persistencia de modelos, así que hay que probarla antes de diseñar nada encima.
- **Si la persistencia de modelos a nivel de base de datos es realmente gratis en OSS.** Se deduce por ausencia en la página de paid-features y por ausencia de callout de plan. Ninguna página de v0.63 lo dice explícitamente.
- **Eficiencia real de `url()` sobre Parquet remoto** en su instancia: si lee por rangos o baja el archivo completo. No medido (el MCP de ClickHouse falló con ConnectionRefused).
- **Densificación de series con breakout en Metabase v0.63.16**: hay bugs abiertos y la doc no lo precisa.