-- Vistas del Postgres espejo.
--
-- Este archivo lo reaplica /srv/bin/espejo-mysis-a-postgres.py al final de cada
-- refresco. Tiene que ser idempotente: se ejecuta entero cada vez.
--
-- Vive aparte del script por un motivo concreto: la primera version del script
-- hacia DROP TABLE de cada tabla antes de copiarla, y en cuanto existio una
-- vista encima de ventas_mysis el DROP empezo a fallar con «cannot drop table
-- because other objects depend on it». El refresco programado se habria caido
-- todas las noches. Ahora el script usa TRUNCATE -que no toca las vistas- y
-- solo recrea desde cero cuando cambian las columnas; en ese caso hace DROP
-- CASCADE y este archivo devuelve las vistas.

-- ---------------------------------------------------------------------------
-- vw_ventas_mysis_periodos
--
-- Espejo fiel de dwh.vw_ventas_mysis_periodos de ClickHouse, INCLUIDO su fallo.
--
-- En ClickHouse el join es v.periodo = CAST(p.periodo, 'String'), y las claves
-- tienen formatos distintos: ventas guarda '2018-05' y periodos guarda 201805.
-- No casa en ninguna de los 1,65 millones de filas. Y como alli esas columnas
-- son Date y no Nullable(Date), el LEFT JOIN sin pareja no deja nulo: rellena
-- con la fecha cero, 1970-01-01. Eso es peor que un nulo, porque parece un dato
-- y un filtro por fecha lo acepta.
--
-- Aqui se replica ese 1970-01-01 con coalesce para que el espejo devuelva
-- exactamente lo mismo que el original: si no, cualquier comparacion entre los
-- dos motores saldria distinta por el motivo equivocado.
--
-- El arreglo de verdad es formatear p.periodo como 'YYYY-MM' en el join, y se
-- comprobo que con eso casan las 1.649.834 filas. Pero hay que hacerlo PRIMERO
-- en ClickHouse, que es la fuente; arreglarlo solo aqui dejaria los dos lados
-- discrepando.
DROP VIEW IF EXISTS mysis.vw_ventas_mysis_periodos;
CREATE VIEW mysis.vw_ventas_mysis_periodos AS
SELECT v.*,
       coalesce(p.start_date, DATE '1970-01-01') AS start_date,
       coalesce(p.end_date,   DATE '1970-01-01') AS end_date
FROM mysis.ventas_mysis v
LEFT JOIN mysis.periodos p ON p.periodo::text = v.periodo;

COMMENT ON VIEW mysis.vw_ventas_mysis_periodos IS
  'Espejo fiel de dwh.vw_ventas_mysis_periodos de ClickHouse, incluido su join roto: start_date y end_date salen 1970-01-01 en todas las filas porque las claves tienen formatos distintos (2018-05 contra 201805). El arreglo va en ClickHouse, no aqui.';

GRANT SELECT ON mysis.vw_ventas_mysis_periodos TO bi_lector;

-- ---------------------------------------------------------------------------
-- Los permisos por defecto ya cubren las tablas nuevas, pero si una se recreo
-- con DROP CASCADE conviene reafirmarlos: cuesta nada y evita un tablero roto
-- por un GRANT que nadie recordo.
GRANT USAGE ON SCHEMA mysis, manual TO bi_lector;
GRANT SELECT ON ALL TABLES IN SCHEMA mysis TO bi_lector;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA manual TO bi_lector;
