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

-- ===========================================================================
-- CAPA «GLOBAL»  ·  11-sep-2026
-- ===========================================================================
--
-- Que es: una sola vista, global.ventas, que junta en el mismo grano -una
-- linea de venta- las dos mitades del negocio:
--
--   MySis  ->  mysis.ventas_mysis   (2018 hasta hoy, 1,65 millones de lineas)
--   ERP    ->  la replica de lectura, por postgres_fdw, SOLO lo nacido aqui
--
-- Para que: los tableros «global» tienen que ver todo el historico y ademas lo
-- que se vendio hace un rato, sin importar en que sistema se vendio. Los
-- tableros «ERP» miran la otra mitad sola, y esos NO pasan por aqui: van
-- directos a la replica.
--
-- Por que FDW y no un ETL de las ventas del ERP: no hay nada que agendar ni
-- desfase que explicar, y sobre todo Sale NO TIENE createdAt ni updatedAt -28
-- columnas, ninguna de auditoria-, asi que un incremental por marca de agua
-- del lado del ERP no tendria de donde agarrarse. El FDW lo lee en vivo.
--
-- Hoy la mitad del ERP devuelve CERO filas y eso es correcto: las 478.475
-- ventas de produccion son migradas, ninguna nacio en el ERP. La vista se
-- llenara sola con el primer despacho.
--
-- Cuando revisar esto: mientras la mitad del ERP este vacia, el coste del FDW
-- es nulo. En cuanto haya decenas de miles de ventas nativas hay que MEDIR
-- global.ventas y, si un tile pasa de dos segundos, materializarla. No antes:
-- una vista materializada aqui es barata de crear y cara de mantener
-- desactualizada.

-- ---------------------------------------------------------------------------
-- 1 - Las tablas del ERP, vistas desde aqui
--
-- Los enum del ERP se recrean AQUI con las mismas etiquetas, y no se
-- declaran TEXT, que fue el primer intento. Motivo medido: postgres_fdw empuja
-- el WHERE al servidor remoto, y alli la columna sigue siendo del enum, asi que
-- `status <> ALL ('{DRAFT,CANCELLED}'::text[])` revienta con «operator does not
-- exist: public."SaleStatus" <> text». El cast explicito no salva: al ser
-- text->text localmente, se borra antes de deparsear. Con el tipo de verdad, el
-- predicado viaja bien tipado y se filtra en el ERP, que es lo que se quiere.
--
-- EL PRECIO: si Prisma añade una etiqueta nueva a cualquiera de estos cuatro
-- enum, hay que añadirla aqui tambien o la lectura de esa fila fallara. Son los
-- unicos sitios de todo el espejo que siguen al esquema del ERP.
CREATE SCHEMA IF NOT EXISTS erp;

DO $enum$ BEGIN
  IF to_regtype('public."SaleStatus"') IS NULL THEN
    CREATE TYPE public."SaleStatus" AS ENUM ('DRAFT','PREPARING','DELIVERED','CANCELLED');
  END IF;
  IF to_regtype('public."SaleDocType"') IS NULL THEN
    CREATE TYPE public."SaleDocType" AS ENUM ('FACTURA','BOLETA','NOTA','GUIA');
  END IF;
  IF to_regtype('public."Origin"') IS NULL THEN
    CREATE TYPE public."Origin" AS ENUM ('NACIONAL','IMPORTADO');
  END IF;
  IF to_regtype('public."ReceivableStatus"') IS NULL THEN
    CREATE TYPE public."ReceivableStatus" AS ENUM ('OPEN','PARTIAL','PAID','OVERDUE','CANCELLED');
  END IF;
  IF to_regtype('public."ReceivableKind"') IS NULL THEN
    CREATE TYPE public."ReceivableKind" AS ENUM ('SALE','MANUAL','DEBIT_NOTE');
  END IF;
  IF to_regtype('public."StockMoveType"') IS NULL THEN
    CREATE TYPE public."StockMoveType" AS ENUM ('RECEIPT','ISSUE','ADJUSTMENT',
      'TRANSFER_IN','TRANSFER_OUT','PICK','RETURN','REVALUATION',
      'ASSEMBLY_OUT','ASSEMBLY_IN');
  END IF;
END $enum$;

-- Se rehacen enteras en cada pasada: es solo metadato, cuesta milisegundos, y
-- asi un cambio de columnas aqui se aplica sin acordarse de borrar nada a mano.
DROP FOREIGN TABLE IF EXISTS
  erp."Sale", erp."SaleLine", erp."Party", erp."ProductVariant", erp."Product",
  erp."ProductFamily", erp."Brand", erp."ProductType", erp."Branch", erp."User",
  erp."Delivery", erp."StockMovement",
  erp."Receivable", erp."InventoryItem", erp."Warehouse" CASCADE;

CREATE FOREIGN TABLE erp."Sale" (
  id text, number text, "customerId" text, "salespersonId" text,
  "sellingBranchId" text, "saleDate" timestamp, status public."SaleStatus", "docType" public."SaleDocType",
  "subtotalOnHand" numeric(18,2), "subtotalPending" numeric(18,2),
  tax numeric(18,2), total numeric(18,2), "legacyRef" text
) SERVER erp_replica OPTIONS (schema_name 'public', table_name 'Sale');

CREATE FOREIGN TABLE erp."SaleLine" (
  id text, "saleId" text, "variantId" text,
  qty numeric(18,4), "qtyDelivered" numeric(18,4),
  "unitPrice" numeric(18,4), total numeric(18,2), "histUnitCost" numeric(18,2)
) SERVER erp_replica OPTIONS (schema_name 'public', table_name 'SaleLine');

CREATE FOREIGN TABLE erp."Party" (
  id text, rut text, "businessName" text
) SERVER erp_replica OPTIONS (schema_name 'public', table_name 'Party');

CREATE FOREIGN TABLE erp."ProductVariant" (
  id text, sku text, "productId" text
) SERVER erp_replica OPTIONS (schema_name 'public', table_name 'ProductVariant');

CREATE FOREIGN TABLE erp."Product" (
  id text, name text, "familyId" text, "brandId" text, "typeId" text, origin public."Origin"
) SERVER erp_replica OPTIONS (schema_name 'public', table_name 'Product');

CREATE FOREIGN TABLE erp."ProductFamily" (id text, name text)
  SERVER erp_replica OPTIONS (schema_name 'public', table_name 'ProductFamily');
CREATE FOREIGN TABLE erp."Brand" (id text, name text)
  SERVER erp_replica OPTIONS (schema_name 'public', table_name 'Brand');
CREATE FOREIGN TABLE erp."ProductType" (id text, name text)
  SERVER erp_replica OPTIONS (schema_name 'public', table_name 'ProductType');
CREATE FOREIGN TABLE erp."Branch" (id text, name text)
  SERVER erp_replica OPTIONS (schema_name 'public', table_name 'Branch');
CREATE FOREIGN TABLE erp."User" (id text, "displayName" text)
  SERVER erp_replica OPTIONS (schema_name 'public', table_name 'User');

CREATE FOREIGN TABLE erp."Delivery" (id text, "saleId" text)
  SERVER erp_replica OPTIONS (schema_name 'public', table_name 'Delivery');
CREATE FOREIGN TABLE erp."StockMovement" (
  id text, "variantId" text, type public."StockMoveType", qty numeric(18,4),
  "totalCost" numeric(18,2), "refType" text, "refId" text
) SERVER erp_replica OPTIONS (schema_name 'public', table_name 'StockMovement');

-- Las tres que necesita el resumen gerencial: cartera para el aging, inventario
-- para el stock valorizado, y bodegas para nombrarlo.
CREATE FOREIGN TABLE erp."Receivable" (
  id text, "customerId" text, "saleId" text,
  amount numeric(18,2), "paidAmount" numeric(18,2), "openAmount" numeric(18,2),
  "dueDate" timestamp, "issueDate" timestamp, "createdAt" timestamp,
  status public."ReceivableStatus", kind public."ReceivableKind",
  "branchId" text, folio bigint, "uncollectibleAt" timestamp, "legacyRef" text
) SERVER erp_replica OPTIONS (schema_name 'public', table_name 'Receivable');

CREATE FOREIGN TABLE erp."InventoryItem" (
  id text, "variantId" text, "warehouseId" text,
  "qtyOnHand" numeric(18,4), "qtyReserved" numeric(18,4), "qtyHeld" numeric(18,4),
  "avgCost" numeric(18,4), "inventoryValue" numeric(18,2), "lastCost" numeric(18,4)
) SERVER erp_replica OPTIONS (schema_name 'public', table_name 'InventoryItem');

CREATE FOREIGN TABLE erp."Warehouse" (
  id text, "branchId" text, code text, name text, active boolean, "isVirtual" boolean
) SERVER erp_replica OPTIONS (schema_name 'public', table_name 'Warehouse');

-- ---------------------------------------------------------------------------
-- 2 - Indices sobre ventas_mysis
--
-- La tabla llegaba SIN NINGUN indice: el volcado nocturno es TRUNCATE + COPY y
-- nadie los habia necesitado. Ahora hacen falta tres:
--   (pid, sku)   el anti-join del refresco incremental
--   ingested_at  la marca de agua de ese mismo refresco; sin el, leerla cuesta
--                105 ms de Parallel Seq Scan sobre 111.752 bloques
--   facturado    el filtro de fecha de todos los tableros
--
-- (pid, sku) va SIN unique a proposito, aunque hoy los 1.654.102 pares sean
-- distintos: el volcado nocturno es un COPY masivo dentro de una transaccion, y
-- un duplicado que se colara en ClickHouse abortaria el refresco entero y
-- dejaria el espejo congelado sin que se note. El incremental desduplica con
-- NOT EXISTS, que no necesita la restriccion.
CREATE INDEX IF NOT EXISTS ventas_mysis_pid_sku_idx      ON mysis.ventas_mysis (pid, sku);
CREATE INDEX IF NOT EXISTS ventas_mysis_ingested_at_idx  ON mysis.ventas_mysis (ingested_at);
CREATE INDEX IF NOT EXISTS ventas_mysis_facturado_idx    ON mysis.ventas_mysis (facturado);

-- ---------------------------------------------------------------------------
-- 3 - La vista unica
--
-- Grano: una linea de venta. Las columnas doc_* son del documento y se repiten
-- en cada linea suya; sumarlas sin DISTINCT infla el total.
--
-- El costo de la mitad del ERP sale de StockMovement via Delivery, igual que en
-- domain/reports/sales-fact.ts del ERP, y NO de SaleLine.histUnitCost: esa
-- columna es del importador y en una venta nativa siempre sera NULL. Usarla
-- daria costo 0 y margen 100%, que es justamente el fallo que hoy tiene la
-- pantalla /reportes/ventas del ERP.
CREATE SCHEMA IF NOT EXISTS global;

DROP VIEW IF EXISTS global.ventas;
CREATE VIEW global.ventas AS
WITH costo_erp AS (
  SELECT d."saleId" AS sale_id, m."variantId" AS variant_id,
         SUM(-m."totalCost")::numeric(18,2) AS costo
  FROM erp."StockMovement" m
  JOIN erp."Delivery" d ON m."refType" = 'Delivery' AND m."refId" = d.id
  WHERE m.type = 'ISSUE'
  GROUP BY 1, 2
)
SELECT
  'MYSIS'::text                     AS origen,
  v.pid::text                       AS documento_id,
  v.factura                         AS documento,
  v.facturado                       AS fecha,
  v.periodo                         AS periodo,
  v.sucursal                        AS sucursal,
  v.vendedor                        AS vendedor,
  v.rut                             AS cliente_rut,
  v.rso                             AS cliente,
  v.sku                             AS sku,
  v.nombre                          AS producto,
  v.familia                         AS familia,
  v.marca                           AS marca,
  v.tipo                            AS tipo,
  v.procedencia                     AS procedencia,
  v.comuna                          AS comuna,
  v.qty                             AS qty,
  v.picking::numeric(18,2)          AS qty_entregada,
  v.pu                              AS precio_unitario,
  v.totaliza_vta                    AS venta,
  v.totaliza_pmp                    AS costo,
  v.margen                          AS margen,
  v.neto                            AS doc_neto,
  v.iva                             AS doc_iva,
  v.total                           AS doc_total,
  NULL::text                        AS estado,
  NULL::text                        AS tipo_documento
FROM mysis.ventas_mysis v

UNION ALL

SELECT
  'ERP'::text,
  s.id,
  s.number,
  s."saleDate"::date,
  to_char(s."saleDate", 'YYYY-MM'),
  b.name,
  u."displayName",
  p.rut,
  p."businessName",
  pv.sku,
  pr.name,
  f.name,
  mk.name,
  tp.name,
  pr.origin::text,
  NULL::text,
  l.qty::numeric(18,2),
  l."qtyDelivered"::numeric(18,2),
  l."unitPrice"::numeric(18,2),
  l.total,
  COALESCE(c.costo, 0)::numeric(18,2),
  (l.total - COALESCE(c.costo, 0))::numeric(18,2),
  (s."subtotalOnHand" + s."subtotalPending")::numeric(18,2),
  s.tax,
  s.total,
  s.status::text,
  s."docType"::text
FROM erp."SaleLine" l
JOIN erp."Sale"           s  ON s.id  = l."saleId"
JOIN erp."ProductVariant" pv ON pv.id = l."variantId"
JOIN erp."Product"        pr ON pr.id = pv."productId"
JOIN erp."Party"          p  ON p.id  = s."customerId"
LEFT JOIN erp."ProductFamily" f  ON f.id  = pr."familyId"
LEFT JOIN erp."Brand"         mk ON mk.id = pr."brandId"
LEFT JOIN erp."ProductType"   tp ON tp.id = pr."typeId"
LEFT JOIN erp."Branch"        b  ON b.id  = s."sellingBranchId"
LEFT JOIN erp."User"          u  ON u.id  = s."salespersonId"
LEFT JOIN costo_erp           c  ON c.sale_id = s.id AND c.variant_id = l."variantId"
WHERE s."legacyRef" IS NULL
  AND s.status NOT IN ('DRAFT', 'CANCELLED');

COMMENT ON VIEW global.ventas IS
  'Ventas de las dos mitades del negocio en el mismo grano de linea: MySis desde 2018 (mysis.ventas_mysis, refrescada cada 15 min) y las nacidas en el ERP (por FDW contra la replica, en vivo). La columna origen dice cual es cual. Las columnas doc_ son del documento y se repiten por linea.';

GRANT USAGE ON SCHEMA global, erp TO bi_lector;
GRANT SELECT ON ALL TABLES IN SCHEMA erp TO bi_lector;
GRANT SELECT ON global.ventas TO bi_lector;
