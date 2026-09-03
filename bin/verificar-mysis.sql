-- Verificacion del DWH de ventas contra MySis.
--
-- Requiere que exista la base mysis_ro (mapeo MySQL de solo lectura), que crea
-- /srv/bin/comparar-mysis.py.
--
-- Los JOIN llevan casts a proposito: en MySis `usr_in` es texto y `user_id` es
-- entero, y `pid` es Int32 alli y UInt64 aqui. MySQL convierte solo; ClickHouse
-- no y falla con «no supertype».

WITH origen AS (
    SELECT toInt64(o.pid) AS pid,
           toString(a.sku) AS sku,
           toDate(o.dt_out) AS facturado,
           (toFloat64OrZero(toString(a.entrega)) + toFloat64OrZero(toString(a.picking)))
             * toFloat64OrZero(toString(a.pu)) AS vta,
           formatDateTime(addMonths(toDate(o.dt_out),
             if(toDayOfMonth(toDate(o.dt_out)) >= 26, 1, 0)), '%Y-%m') AS periodo
    FROM mysis_ro.mstr_pedidos o
    INNER JOIN mysis_ro.mstr_pedidos_aux a ON o.pid = a.pid
    INNER JOIN mysis_ro.tab_sku s ON a.sku = s.sku
    INNER JOIN mysis_ro.tab_clientes p ON toInt64OrNull(toString(o.cliente_id)) = toInt64OrNull(toString(p.cliente_id))
    INNER JOIN mysis_ro.tab_users u ON toInt64OrNull(toString(o.usr_in)) = toInt64OrNull(toString(u.user_id))
    INNER JOIN mysis_ro.tab_bodegas bo ON toInt64OrNull(toString(o.sucursal_id)) = toInt64OrNull(toString(bo.bodega_id))
    WHERE o.factura IS NOT NULL
      AND toInt64OrNull(toString(o.direccion_id)) != 0
      AND toDate(o.dt_out) >= toDate('2025-11-01')
),
espejo AS (
    SELECT toInt64(pid) AS pid, toString(sku) AS sku, facturado, periodo,
           toFloat64(totaliza_vta) AS vta
    FROM dwh.ventas_mysis
    WHERE facturado >= toDate('2025-11-01') AND sku != ''
)
SELECT 'lineas comparadas'                       AS control, toString(count()) AS valor
FROM espejo e INNER JOIN origen o ON e.pid = o.pid AND e.sku = o.sku
UNION ALL
SELECT 'en MySis y NO en el DWH (deben ser 0)', toString(count())
FROM origen WHERE (pid, sku) NOT IN (SELECT (pid, sku) FROM espejo)
UNION ALL
SELECT 'con facturado distinto (deben ser 0)', toString(count())
FROM espejo e INNER JOIN origen o ON e.pid = o.pid AND e.sku = o.sku
WHERE e.facturado != o.facturado
UNION ALL
SELECT 'con periodo distinto (deben ser 0)', toString(count())
FROM espejo e INNER JOIN origen o ON e.pid = o.pid AND e.sku = o.sku
WHERE e.periodo != o.periodo
UNION ALL
SELECT 'con venta distinta (deben ser 0)', toString(count())
FROM espejo e INNER JOIN origen o ON e.pid = o.pid AND e.sku = o.sku
WHERE abs(e.vta - o.vta) > 0.5
UNION ALL
SELECT 'en el DWH y NO en MySis (huerfanas)', toString(count())
FROM espejo WHERE (pid, sku) NOT IN (SELECT (pid, sku) FROM origen)
FORMAT PrettyCompactMonoBlock;
