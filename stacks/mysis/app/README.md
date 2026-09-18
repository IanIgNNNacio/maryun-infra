# El cambio de MySis para leer y escribir en R2

Dos archivos nuevos y dos sustituciones mecánicas. **Nada de esto está aplicado
todavía**: es la fase 3 del plan de [`../../../docs/migracion-mysis-a-ovh.md`](../../../docs/migracion-mysis-a-ovh.md),
y va después de tener MySis levantado en OVH y antes del corte.

## Por qué es tan poco

El relevamiento del 18-sep-2026 encontró que **ningún PHP lee jamás un archivo de
REPO**. Cero `readfile()`, cero `file_get_contents()`, cero `fopen()`. Apache los
sirve como estáticos y el código sólo construye cadenas de URL.

| | |
|---|---|
| escrituras | **34 sentencias**, en dos directorios |
| borrados | **cero** — el corpus es *append-only* |
| lecturas por PHP | **cero** |
| cadenas de URL | **116 líneas en 60 archivos** |

Y como el nombre del archivo es `{tipoDTE}_{folio}.{ext}` y
`mstr_pedidos.factura_pdf` ya guarda exactamente ese basename, **la clave del
objeto en R2 es el nombre de hoy**: no hay que tocar el esquema ni mantener una
tabla de equivalencias.

## Dónde va cada archivo

| aquí | en el árbol de MySis |
|---|---|
| `lib-repo.php` | `/var/www/html/mryn/lib/repo.php` |
| `REPO.php` | `/var/www/html/mryn/REPO.php` |

## Las dos sustituciones

**Escritura — 34 líneas.** Las 26 de `OPENFACTURA/`:

```php
- file_put_contents($sale, $bin);
+ require_once __DIR__ . '/../lib/repo.php';
+ repo_put(basename($sale), $bin);
```

Y las 8 de `FACTURADOR/`, que hoy hacen que mPDF o FPDF escriban el archivo
directamente:

```php
- $pdf->Output('F', '../REPO/' . $nombresale);
+ repo_put($nombresale, $pdf->Output('S'));
```

`Output('S')` devuelve el PDF como cadena en vez de escribirlo. Es la misma
biblioteca y la misma llamada, con otra letra.

**Lectura — 116 cadenas.** Un `sed`, siempre el mismo patrón:

```php
- "<a href='../../REPO/" . $row['factura_pdf'] . "' target='_blank'>"
+ "<a href='" . repo_url($row['factura_pdf']) . "' target='_blank'>"
```

Los 60 archivos afectados están listados en el relevamiento; el prefijo relativo
(`../`, `../../`) cambia según dónde esté la página, y `repo_url()` lo recibe
como segundo argumento.

## Lo que hay que comprobar antes de darlo por bueno

1. **Emitir un DTE completo** en el entorno nuevo y ver que el PDF aparece en R2
   *y* en disco, y que la pantalla lo abre.
2. **Abrir un documento viejo** —de 2019, por ejemplo— que sólo esté en R2.
3. **Entrar sin sesión** a `REPO.php?f=33_1000000.pdf` y comprobar que devuelve
   403. Hoy la ruta equivalente devuelve el PDF.
4. **Cortar la red a R2** a propósito y emitir: tiene que seguir emitiendo,
   dejando el nombre en `/tmp/repo-pendientes.txt`.

## Dos decisiones que están tomadas y por qué

**Doble escritura encendida.** MySis pide el folio al SII y **después** escribe el
archivo. Si la escritura falla, el folio ya se consumió y queda un documento
tributario emitido sin PDF. Hoy eso sólo pasa si se llena el disco; con R2 pasa
también si parpadea la red. Escribir en los dos sitios cuesta una línea y permite
volver atrás cambiando una constante. Se apaga cuando haya semanas de evidencia.

**Un endpoint que redirige, no un bucket público.** La alternativa —`repo_url()`
devolviendo `https://docs.maryun.cl/33_1047488.pdf`— cuesta **exactamente las
mismas ediciones**. No se hace porque hoy los DTE ya se descargan sin
autenticarse y los folios son secuenciales: un bucket público convertiría «quien
alcance el servidor» en «todo internet». El endpoint cierra ese agujero de paso,
y como redirige en vez de servir, PHP no transporta un solo byte.

## Lo que NO se hizo, y es a propósito

No se tocan `mod_rewrite` ni `mod_proxy`. Hay un diseño alternativo —dejar el
código intacto y que Apache busque en R2 lo que no esté en disco— que no requiere
editar PHP, pero **exige esos dos módulos, que hoy no están cargados en
producción**. Encender módulos de Apache durante una mudanza es cambiar dos cosas
a la vez.
