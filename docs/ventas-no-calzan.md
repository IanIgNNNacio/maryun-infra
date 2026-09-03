# Por qué las ventas de Metabase no calzan con MySis

Revisado el 2 de septiembre de 2026, a raíz de que el jefe de Ian reportara que
los números no cuadran.

**Conclusión corta:** los datos están bien. De 358.144 líneas comparadas contra
MySis, **ninguna faltaba y ninguna tenía un monto distinto**. Lo que no calza son
dos definiciones y un defecto pequeño.

---

## 1. La causa grande: las notas de crédito están mezcladas con las ventas

`dwh.ventas_mysis` no es una tabla de ventas. El cargador
`dl_ventas_mysis.sql` es un `UNION ALL` de tres cosas:

| bloque | origen en MySis | qué aporta |
|---|---|---|
| ventas | `mstr_pedidos` + `mstr_pedidos_aux` | positivo |
| notas de crédito | `mstr_nc` + `mstr_nc_aux` | **negativo** |
| anexos | `mstr_anexo` | comisiones, monto 0 |

Y **no hay ninguna columna que diga de cuál viene cada fila.**

Medido sobre cinco períodos (2025-09 a 2026-01):

| | monto |
|---|---|
| Ventas según MySis | $6.451.181.434 |
| Lo que suma `ventas_mysis` | $5.953.411.545 |
| Notas de crédito incluidas ahí | **−$503.666.879** |
| Ventas sin las notas de crédito | $6.457.078.424 |

Ahí está el 6 % a 9 % que se ve mes a mes. **No es un error de carga: es que el
tablero está mostrando la venta neta de devoluciones y se está comparando contra
la venta bruta de MySis.**

Las dos cifras son legítimas; lo que falta es poder elegir. Hoy no se puede,
porque no hay cómo separarlas sin salir a preguntarle a MySis.

**Lo que hay para distinguirlas, y por qué no basta.** El bloque de notas de
crédito escribe siempre `shopify = ''`, `facturar` vacío, `deuda = 0` y los
montos negativos. Pero hay 5.870 filas de nota de crédito y sólo 5.737 con monto
negativo: 133 no se pescan por el signo. Una regla por signo se equivoca en el
2 % de ellas.

**Lo correcto es añadir una columna** —`origen` con valores `VENTA`,
`NOTA_CREDITO`, `ANEXO`— en el propio `UNION` del cargador. Es un cambio de
esquema y de pipeline, así que queda propuesto, no hecho.

## 2. La segunda causa: `periodo` no es el mes calendario

La columna es `MATERIALIZED` y se calcula así:

```sql
periodo = formatDateTime(
    addMonths(facturado, if(toDayOfMonth(facturado) >= 26, 1, 0)),
    '%Y-%m')
```

Es un **período comercial que corre del 26 al 25**: todo lo facturado el día 26 o
después cuenta para el mes siguiente.

Es una decisión de negocio, no un error. Pero significa que comparar el
«noviembre» de Metabase contra `WHERE MONTH(dt_out) = 11` en MySis da unas
**1.200 líneas de diferencia por mes**, y no hay nada roto. Le pasó a esta misma
revisión antes de darse cuenta.

## 3. El defecto real: las filas nunca se actualizan

`de_ventas_mysis.py` inserta con un **anti-join por `(pid, sku)`**: pregunta a
ClickHouse qué pares ya existen y sólo inserta los que faltan.

Eso evita duplicados —y por eso el pipeline puede correr cada 5 minutos sin
inflar la tabla—, pero tiene una consecuencia que no está escrita en ninguna
parte: **una fila que ya entró no se vuelve a mirar nunca.** Si en MySis cambia
la fecha de facturación, la cantidad o el número de factura, el DWH se queda con
la primera versión para siempre.

Y el cargador sólo mira los últimos `dias` días (hoy, 5), así que pasada esa
ventana ya nadie vuelve a pasar por ahí.

Medido sobre 358.144 líneas desde agosto de 2025:

| | líneas | monto |
|---|---|---|
| con `facturado` desactualizado | **136** | $11.636.127 |
| de ésas, que caen en otro mes | **86** | $9.980.435 |
| además con cantidad distinta | 25 | |
| además con número de factura distinto | 42 | |
| en el DWH y ya no en MySis | 13 | $102.496 |

Es el 0,04 % de las líneas. Pequeño, pero explica por qué los meses recientes
bailan: septiembre y octubre de 2025 cuadran **al peso** ($1 y $2 de diferencia
en mil millones), noviembre se va 0,008 %, diciembre −0,85 % y enero +1,25 %.
Cuanto más reciente el mes, más probable es que una línea haya cambiado después
de que el DWH la congelara.

### Qué se hizo

1. Se respaldaron las 149 filas afectadas en `dwh.respaldo_desfase_2026_09_02`
   (136) y `dwh.respaldo_huerfanas_2026_09_02` (13).
2. Se borraron de `dwh.ventas_mysis`, para que el anti-join deje de saltárselas.
3. Se corrió el pipeline con una ventana de 300 días para que las traiga de
   nuevo, esta vez completas.

Las 13 huérfanas no vuelven: ya no existen en MySis.

### Qué falta decidir

El anti-join sigue igual, así que **esto se va a volver a acumular**. Las salidas
posibles, de menos a más trabajo:

- **Una pasada de reconciliación periódica** —semanal— que compare contra MySis y
  arregle lo que se haya movido. Es lo que se acaba de hacer a mano; dejarlo
  programado es barato.
- **Cambiar el motor a `ReplacingMergeTree`** con clave `(pid, sku)` y una
  columna de versión. Entonces reinsertar reemplaza en vez de duplicar, y el
  anti-join sobra. Es un cambio de tabla, con recarga.
- **Ampliar la ventana** de 5 días a 30 o 60. Reduce la deriva pero no la
  elimina, y no arregla lo que cambie después.

## 4. Un detalle aparte: `qty` puede llegar mal

La consulta origen devuelve las cantidades **formateadas como texto chileno**:

```sql
FORMAT(a.qty, 0, 'es_CL') AS qty     -- 1234 sale como "1.234"
```

`qty` **no** está en la lista `DECIMAL_COLS` del transformador, así que no se
limpia ahí. Llega al exportador, que hace `pd.to_numeric("1.234")` y lo lee como
**1,234** a la inglesa.

Hoy hay 157 filas con cantidad no entera, y 155 de ellas están entre 1 y 10 —
que es exactamente la forma que tendría el error. No están todas mal: hay 1.341
filas con cantidad sobre 1.000 bien guardadas, así que el camino no siempre pasa
por ahí. Conviene revisarlo, pero es chico y no explica lo que ve el jefe.

**Y un error de tipeo en el mismo archivo**, que conviene arreglar aunque hoy no
haga daño visible:

```python
DECIMAL_COLS = {
    'pu','pmp','totaliza_pmp','totaliza_vta','margen','diferencia', 'deuda'
    'totaliza_diferencia','margen_diferencia', ...
}
```

Falta la coma después de `'deuda'`. Python concatena las dos cadenas, así que el
conjunto contiene `'deudatotaliza_diferencia'` y **no contiene ni `deuda` ni
`totaliza_diferencia`**. Las dos se tratan como texto en vez de decimal.

## 5. Cómo repetir esta comprobación

```bash
sudo /srv/bin/comparar-mysis.py            # monta MySis y compara
sudo /srv/bin/comparar-mysis.py --limpiar  # además desmonta al terminar
```

Monta MySis dentro de ClickHouse como base de solo lectura (`mysis_ro`, motor
MySQL, que es un mapeo de conexión y no copia nada) y hace las cuentas del mismo
lado, para que no haya diferencias de redondeo entre dos clientes distintos.

**Deja el mapeo montado** salvo que se le pase `--limpiar`. Conviene desmontarlo
cuando no se use: es una conexión viva a la base de producción del ERP.

---

## 6. Resultado de la corrección (2 de septiembre de 2026)

### Qué se corrigió

| | filas | qué eran |
|---|---|---|
| `facturado` desactualizado | 136 | se borraron y se recargaron desde MySis |
| filas duplicadas | **1.396** | mismo `(pid, sku)` repetido; se conservó la copia más reciente |
| huérfanas | 18 | 13 + 5; pedidos o líneas que ya no existen en MySis |

**Los duplicados fueron el hallazgo grande y no estaban buscados.** Había 658
pares `(pid, sku)` repetidos, con 1.396 filas de más. En el período 2026-01
sumaban $5.794.503, que era **exactamente** la diferencia que quedaba tras la
recarga.

No los produjo esta intervención: se ingestaron el 2026-09-01 (502), el
2025-12-31 (136) y el 2025-12-26 (1.416). Ninguno lleva fecha de hoy.

Vienen de que **el anti-join no es atómico**: dos ejecuciones simultáneas del
pipeline pueden ver el mismo par como ausente y ambas insertarlo. Con un trigger
cada 5 minutos y otro nocturno, eso pasa.

### Cómo quedó

Venta por período comercial, MySis contra el DWH, sólo líneas de venta:

| período | MySis | DWH | diferencia |
|---|---|---|---|
| 2025-11 | 1.333.001.913 | 1.333.001.911 | −2 |
| 2025-12 | 1.141.613.785 | 1.141.613.782 | −3 |
| 2026-01 | 1.259.795.823 | 1.259.795.821 | −2 |
| 2026-02 | 1.358.086.192 | 1.358.086.187 | −5 |
| 2026-03 | 1.338.738.435 | 1.338.738.431 | −4 |
| 2026-04 | 1.449.202.130 | 1.449.202.125 | −5 |
| 2026-05 | 1.082.155.120 | 1.082.155.117 | −3 |
| 2026-06 | 1.073.790.645 | 1.073.619.490 | **−171.155** |
| 2026-07 | 910.447.416 | 910.447.414 | −2 |
| 2026-08 | 985.453.488 | 985.453.482 | −6 |

Nueve de diez cuadran con diferencias de **2 a 6 pesos** sobre mil millones, que
es redondeo.

El que falta, 2026-06, se desvía **0,016 %** y ya está explicado: hay una línea
de venta legítima con monto **negativo** —SCALEAQ CHILE SPA, −$200.000— y
cualquier filtro de «sólo ventas» basado en el signo la trata de forma
inconsistente. Es el mismo problema estructural del punto 1: sin una columna que
diga qué es cada fila, no hay filtro que acierte siempre.

### Respaldos

Nada se borró sin copia:

| tabla | filas |
|---|---|
| `dwh.respaldo_desfase_2026_09_02` | 136 |
| `dwh.respaldo_duplicados_2026_09_02` | 2.054 |
| `dwh.respaldo_huerfanas_2026_09_02` | 13 |
| `dwh.respaldo_huerfanas2_2026_09_02` | 5 |

Se pueden borrar cuando haya confianza en el resultado.

### Lo que va a volver a pasar si no se cambia nada

Esta corrección fue a mano y **no arregla la causa**. Sigue pendiente:

1. **Los duplicados vuelven.** El anti-join no es atómico y hay dos triggers
   activos sobre el mismo pipeline: uno cada 5 minutos y otro nocturno.
2. **La deriva vuelve.** Una fila que ya entró no se vuelve a mirar nunca.
3. **Las notas de crédito siguen sin marcar.**

Las tres se resuelven con el mismo cambio: pasar `dwh.ventas_mysis` a
**`ReplacingMergeTree`** con clave `(pid, sku)` y una columna de versión
—`ingested_at` sirve—, más una columna `origen` con `VENTA`, `NOTA_CREDITO` o
`ANEXO` escrita en cada rama del `UNION`. Con eso, reinsertar reemplaza en vez
de duplicar, el anti-join sobra, y el tablero puede elegir qué sumar.

Es un cambio de tabla con recarga completa, así que no se hizo sin decisión.

---

## 7. De dónde salen realmente esos casos (revisado contra el código de MySis)

Tres preguntas de Ian, contestadas leyendo `maryun-mysis/mryn` y consultando
MySis **en solo lectura**.

### 7.1 ¿Por qué había filas con `facturado` desactualizado, si el ETL no se equivoca?

No se equivoca. **`dt_out` cambia en MySis después de que el ETL ya pasó.**

En `API/creafactura.php` conviven las dos ramas:

```php
// 272 — al emitir la GUIA
update mstr_pedidos set guia='$elfolio', ..., dt_out=now() where pid in ($pid)
// 274 — despues, al emitir la FACTURA del mismo pedido
update mstr_pedidos set factura='$elfolio', ..., dt_out=now() where pid in ($pid)
```

Un pedido despachado con guía en mayo y facturado en julio tiene `dt_out` de
mayo primero y de julio después. El mismo patrón está en `fuerza.php`,
`creand.php` (notas de débito), `creafacturadeguia.php`, `creaRefactura.php` y
los `DTE*` de OpenFactura. Y `DTEfactura_manual_no_sii.php:370` va más lejos:
fija `dt_out='$fecha_emite'`, una fecha que teclea una persona.

El ETL mira los últimos **5 días** y **nunca vuelve a mirar** una fila que ya
insertó. Si el segundo `dt_out=now()` ocurre pasada esa ventana, el DWH se queda
con el primero para siempre. No es un error de lógica del ETL: es que su diseño
supone que los datos no cambian, y sí cambian.

### 7.2 ¿Cuándo se borra un pedido?

Hay dos caminos, los dos con guardas:

- `pages/pedidos/cancelacotizacion.php` — cancelar una cotización. Sólo borra si
  **no tiene factura** y **no tiene picking**.
- `pages/almacenaje/deltraspaso.php` — borrar un traspaso interno. Sólo si
  `picking == 0`.

**MySis archiva antes de borrar**, y eso está bien hecho:

```php
INSERT INTO mstr_pedidos_borrados     SELECT * FROM mstr_pedidos     WHERE pid=$gid;
INSERT INTO mstr_pedidos_aux_borrados SELECT * FROM mstr_pedidos_aux WHERE pid=$gid;
```

Hay **33.171 pedidos archivados**, ninguno con factura, coherente con la guarda.

Aparte, las **líneas** se borran y se reinsertan como rutina cada vez que se
edita un pedido: `grabar_pedido.php:169` borra las líneas sin picking antes de
regrabar, y `abreguia.php`, `grabar_guia.php` y `cancelanv.php` borran todas las
líneas del pedido. Por eso una línea puede desaparecer sin que desaparezca el
pedido.

**El caso concreto: pedido 1122070.**

| | en el DWH | en `mstr_pedidos_borrados` |
|---|---|---|
| cliente | DISTRIBUIDORA OSCAR JOSE FICA DELGADO EIRL | mismo |
| sucursal | CONCEPCION | 3 |
| creado | | 2026-02-04 08:39:48 |
| cerrado | | 2026-02-04 08:41:30 |
| **factura** | **988659** | **NULL** |
| **facturado** | **2026-02-19** | **NULL** |
| neto | | 77.700 |

Tres líneas de BOTIN NG 572 AC, $25.900 cada una.

La secuencia se lee sola: se creó y cerró el 4 de febrero, se facturó (988659,
`dt_out` 2026-02-19), **el ETL lo capturó así**, después **se le quitó la
factura** —volvió a NULL— y recién entonces se pudo cancelar y archivar. El DWH
se quedó con la foto de cuando tenía factura, y siguió contando esa venta.

### 7.3 ¿No son normales las líneas con saldo negativo?

**En MySis no existen.** De 327.764 líneas de venta del último año: **cero** con
precio unitario negativo y **cero** con cantidad negativa.

Las negativas del DWH son notas de crédito, que el cargador multiplica por −1.
Pero al rastrear una apareció algo peor.

**`pid` no es único entre tablas.**

La fila del DWH decía: pid 43156, factura 64992, facturado 2026-05-26, HOSPITAL
DE ACHAO. En MySis, `mstr_pedidos.pid = 43156` es **otro pedido**: factura
268431, `dt_out` **2018-10-19**, otros SKU.

Medido:

| | |
|---|---|
| pids distintos en `mstr_pedidos` | 1.170.232 |
| pids distintos en `mstr_nc` | 44.815 |
| **pids que existen en las dos** | **44.815 — el 100 %** |
| pares `(pid, sku)` que chocan | **215** |

Y el DWH usa **`(pid, sku)` como clave natural** en el anti-join del exportador.

**De los 215 pares que chocan, 212 tienen una sola fila en el DWH donde debería
haber dos.** O sea: **212 registros perdidos en silencio** —una venta o su nota
de crédito— porque cuando llegó el segundo, la clave ya estaba ocupada y el
anti-join lo dio por insertado.

Eso no lo arregla ninguna recarga: mientras la clave sea `(pid, sku)`, el
segundo registro nunca va a entrar.

### 7.4 Corrección de algo que se dijo antes

En el punto 6 se atribuyó la diferencia de 2026-06 a «una línea de venta
legítima con monto negativo». **Es falso.** Esas líneas negativas no son ventas:
son notas de crédito cuyo `pid` choca con el de un pedido, y por eso ningún
filtro basado en `pid IN mstr_nc` las clasifica bien.

Las 5 filas huérfanas que se borraron sí estaban bien borradas: se comprobó que
ninguna existe hoy en `mstr_pedidos_aux` **ni** en `mstr_nc_aux`.

### 7.5 Lo que esto cambia en la solución

La clave natural tiene que incluir **de qué tabla viene el registro**. La
propuesta del punto 6 se corrige así:

```sql
-- ReplacingMergeTree con version, y la clave completa
ORDER BY (origen, pid, sku)
```

donde `origen` es `VENTA`, `NOTA_CREDITO` o `ANEXO`, escrito en cada rama del
`UNION` del cargador. Sin esa columna no hay clave que sirva: hoy dos registros
distintos comparten identidad, y uno de los dos se pierde.
