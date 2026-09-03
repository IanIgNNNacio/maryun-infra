# Fin del período comercial del 26 al 25

Aplicado el 3 de septiembre de 2026. **Los períodos pasan a ser meses
calendario.** La regla anterior —el mes iba del 26 de un mes al 25 del
siguiente— existía para calcular las comisiones de los vendedores sobre ese
ciclo, y ya no se usa.

## Lo que se cambió

**ClickHouse**

| objeto | antes | ahora |
|---|---|---|
| `dwh.ventas_mysis.periodo` | `formatDateTime(addMonths(facturado, if(toDayOfMonth(facturado) >= 26, 1, 0)), '%Y-%m')` | `formatDateTime(facturado, '%Y-%m')` |
| `dwh.ventas_mysis_2.periodo` | igual | igual |
| `dwh.mv_facturas_periodos` | calculaba el ciclo 26-25 | mes calendario |
| `dwh.periodos` | 100 filas del 26 al 25 | 100 filas del 1 al último día |
| `dwh.vw_ventas_mysis_periodos` | — | recreada, ver abajo |

Las dos columnas son `MATERIALIZED`, así que además de cambiar la fórmula hubo
que reescribir el dato con `ALTER TABLE ... MATERIALIZE COLUMN`. Se comprobó:
**cero filas** donde `periodo` no sea el mes de `facturado`.

**Metabase** — 1 tarjeta: la 41, «comparacion mes actual / anterior», del
tablero *Detalles Ventas*. Agrupaba por mes de ciclo; ahora por
`toStartOfMonth(facturado)`.

**Superset** — 7 conjuntos de datos: Top Productos, Top Clientes, Margen de
Ventas, Top Vendedores del Mes, Mejor Sucursal del Mes y los dos de
«Comparación ventas por ciclo». Los cinco primeros acotaban la ventana con
`CASE WHEN DAY(CURDATE()) >= 26 ...`; ahora usan `toStartOfMonth(today())` y
`toLastDayOfMonth(today())`.

**Mage y el ERP** — sin cambios. La regla no vivía ahí.

## De paso: la vista quedó arreglada y seis veces más rápida

`vw_ventas_mysis_periodos` se recreó, y aprovechando se corrigieron dos cosas
que arrastraba:

1. **Su `JOIN` no casaba nunca.** Comparaba `v.periodo`, que es `'2026-08'`,
   contra `CAST(p.periodo,'String')`, que es `'202608'`. Y como `start_date` y
   `end_date` son `Date` y no `Nullable(Date)`, el `LEFT JOIN` sin pareja
   rellenaba con la fecha cero: **1970-01-01 en el 100 % de las filas**. Ahora
   la clave se construye igual en los dos lados, y hay 100 fechas distintas.
2. **Su `GROUP BY` sobre las 57 columnas** sólo estaba para que `any()` se
   quedara con una fila de la derecha. No hace falta, porque `dwh.periodos`
   tiene una fila por período. Quitarlo bajó una suma completa de **~570 ms a
   92 ms**.

La vista conserva sus 57 columnas, así que nada que la consuma se rompe.

## Qué esperar en las cifras

**El total no se mueve. El reparto entre meses sí, y bastante.**

| mes | antes (ciclo 26-25) | ahora (mes calendario) | |
|---|---|---|---|
| 2026-01 | 1.158.772.805 | 1.247.235.451 | +7,6 % |
| 2026-02 | 1.271.052.873 | 1.191.615.020 | −6,2 % |
| 2026-03 | 1.216.834.940 | 1.398.045.927 | **+14,9 %** |
| 2026-04 | 1.297.232.922 | 1.235.297.398 | −4,8 % |
| 2026-05 | 979.734.799 | 889.352.277 | −9,2 % |
| 2026-06 | 1.003.554.520 | 908.528.606 | −9,5 % |
| 2026-07 | 850.712.202 | 980.362.968 | **+15,2 %** |
| 2026-08 | 892.657.554 | 869.449.655 | −2,6 % |

Total histórico: **61.867.876.132** antes, **61.868.199.874** después. La
diferencia son las filas que entraron por el pipeline de 5 minutos entre una
medición y otra.

**Aviso sobre el mes en curso.** El 3 de septiembre, el período `2026-09` pasó
de $320 M a $86 M. No es un error: antes contenía desde el 26 de agosto y ahora
sólo los tres días de septiembre transcurridos. Va a parecer bajo hasta que el
mes avance.

## Respaldos

| qué | dónde |
|---|---|
| contenido de `dwh.periodos` | `dwh.respaldo_periodos_regla26_2026_09_03` |
| totales por período de antes | `dwh.respaldo_totales_regla26_2026_09_03` |
| SQL de Metabase y Superset | `/srv/secrets/regla26-antes-2026-09-03.json` |

## Herramientas

```bash
sudo /srv/bin/quitar-regla-26.py            # informa, no aplica
sudo /srv/bin/quitar-regla-26.py --hazlo    # aplica
sudo /srv/bin/verificar-regla-26.py         # comprueba que no queda nada y que todo corre
```

El verificador **ejecuta** cada consulta, no sólo busca el texto: un reemplazo
puede dejar SQL sintácticamente roto y eso sólo se ve al correrlo.

Los 18 conjuntos de datos que ese verificador reporta como fallidos llevan
plantillas Jinja de Superset (`{% set ... %}`, `url_param`, `get_time_filter`) y
no pueden ejecutarse fuera de Superset, que las renderiza antes. No están
tocados por este cambio; ninguno de los 7 modificados aparece entre ellos.
