# Mover MySis de Azure a maryun01, con los archivos en R2

Relevado el 18 de septiembre de 2026, con la VM libre por los feriados y con
autorización expresa de Ian para cargarla. Todo lo que sigue está **medido**, no
supuesto; donde no hay número, lo dice.

---

## 0 · Lo urgente, antes que la migración

**La VPS comprometida tiene una sesión SSH permanente contra MySis.**

`51.222.28.249` —`vps-39c1d871.vps.ovh.ca`, la máquina de la que se migró el ERP
por estar comprometida y que sigue encendida— mantiene una sesión SSH abierta
contra la VM de MySis y **se reconecta en cada arranque**:

```
Sep 17 08:30:14  Accepted publickey for mrootuser from 51.222.28.249
Aug 31 19:55:45  idem
Aug 18 19:06:46  idem
```

Peor: **hay una sola clave SSH** en `authorized_keys` de `mrootuser` —RSA 3072,
`generated-by-azure`— y con ella entran el túnel de OVH, esa máquina, y las
personas. Una clave para todo.

Nadie sabe qué corre ahí. Es una dependencia viva sin identificar **y** un
acceso desde una máquina que se dio por perdida. Esto se resuelve antes de
planificar nada:

1. Averiguar qué proceso de esa máquina mantiene la sesión.
2. Si no hace falta, apagar la VPS —ya era el pendiente número uno— y **rotar la
   clave de MySis**, dando una distinta a cada consumidor: una para el túnel de
   OVH, otra para las personas.

---

## 1 · Qué es MySis hoy

| | |
|---|---|
| Máquina | Azure `vmmysis`, `20.153.168.52`, 4 vCPU, 15 GB RAM, disco 497 GB |
| Pila | Apache 2.4.52 · PHP 7.4.33 · MariaDB 10.6.23 · Ubuntu 22.04 |
| Aplicación | `/var/www/html/mryn` + `/var/www/html/externo` |
| Tráfico | **34.490 peticiones/día**, 110 IP distintas, pico de 354/min |

### Lo que ocupa

| | |
|---|---|
| `mryn/REPO/` | **123 GiB · 943.720 archivos** |
| `pages/rendicion` · `pages/mail` | 6,1 GB · 6,0 GB |
| `pages/solicitud` · `salidas_excel` | 686 MB · 154 MB |
| código y librerías | ~1,5 GB |
| MariaDB `mryn_data` | 10,56 GB en 168 tablas |
| `/home/mrootuser/Back_my` | 40 GB de respaldos — **no migra** |

**De 213 GB, lo que hay que mover son ~14 GB.** El resto son archivos que van a
R2 una vez y no vuelven a moverse.

### Lo que la máquina usa de verdad

Medido en horario laboral: **1.121 MB de RAM** para el sistema entero, MariaDB
467 MB, Apache 158 MB en ocho procesos, carga 0,81 sobre 4 núcleos. Y de esos
1.121 MB, unos 240 son agentes de Azure que en OVH no existirán.

**MySis no necesita 4 núcleos y 15 GB: necesita 2 vCPU y 4 GB.** Los 15 GB de
Azure son, casi todos, caché de página para los 123 GiB de archivos.

---

## 2 · El código: la copia local servía a medias

El árbol de producción es un repositorio git, pero **git no es la fuente de
verdad y GitHub tampoco**:

| | |
|---|---|
| Último commit | `1e8d182` — **11-sep-2025**, hace un año |
| Modificados sin commitear | 148 |
| Borrados que git aún cree vivos | 5.885 |
| Sin seguir | 977.257 |
| `.gitignore` | **no existe** |
| Permisos del árbol y de `.git` | **777** |

Un `git clone` daría un árbol sin nada de lo escrito desde septiembre de 2025 y
con 5.885 archivos que producción ya borró. **Lo que se despliega es el árbol de
trabajo.**

### Drift real desde el rescate del 27-jul-2026

Se compararon 25.580 archivos por **md5**, no por tamaño y fecha. De 28
diferencias, 21 son los `.bak` que creó `set_local_db.ps1` del propio Ian. **El
drift real son 10 archivos**, y uno importa:

| fecha | archivo | |
|---|---|---|
| 2026-08-23 | `pages/login.php` | modificado |
| 2026-08-22 | `pages/changePass_old.php` | nuevo |
| 2026-08-23 | `pages/changePass.php` | modificado |
| 2026-08-20 | `pages/ingresos/calculapmp_ver_new.php` | nuevo |
| 2026-07-29 | `pages/preparacion/` — 4 archivos | modificados |
| — | `pages/login_sinip.php` | **nuevo** |

`login_sinip.php` pesa 6.036 bytes, **exactamente lo que pesa el `login.php` de
la copia de julio**. O sea: alguien guardó el login viejo como «sin IP» y escribió
uno nuevo. **Hay una restricción por IP en el login que la copia local no tiene**,
y eso muerde justo al cambiar de servidor.

### Lo que el rescate de julio excluyó por error

Las cuatro carpetas descartadas como «adjuntos y uploads» **contienen 93
archivos de código de negocio**: `pages/rendicion` 39, `pages/mail` 30,
`pages/solicitud` 21, `salidas_excel` 3. Y **`/var/www/html/externo` entera** —el
portal público de registro de clientes, 125 MB— no estaba en la copia en
absoluto.

Corregido: `mryn_code_20260918.tar.gz` (734 MB, 61.050 archivos) más
`mryn_faltante_20260918.tar.gz` (26 MB, 11.252 archivos), los dos verificados
por md5 en origen, tránsito y destino.

---

## 3 · Los archivos: mejor noticia de lo esperado

### La forma

**`REPO` es un único directorio plano con 943.720 archivos.** Cero
subdirectorios. El inodo del directorio pesa 32 MB: cada `readdir` lee 32 MB de
entradas.

El nombre es `{tipoDTE}_{folio}.{pdf|PNG|xml}` en el **99,999 %** de los casos, y
`mstr_pedidos.factura_pdf` guarda exactamente ese basename. **La clave de R2
puede ser literalmente el nombre de hoy, y no hace falta tocar el esquema.**

| tipo | archivos | |
|---|---|---|
| 33 · factura | 601.739 | 83,13 GiB |
| 39 · boleta | 194.655 | 23,55 GiB |
| 52 · guía | 107.859 | 12,28 GiB |
| 61 · nota de crédito | 39.413 | 4,05 GiB |

**Ningún archivo pasa de 1 MB.** 815.732 PDF de 157 KB de media y 127.807 PNG de
5,6 KB —los timbres del SII—.

### La antigüedad: el `mtime` miente

Por `mtime` parecería que 663.007 archivos son de 2025. No lo son: `migra.php:74`
los descargó desde `http://45.33.16.82/mryn/REPO/`, un Linode anterior. La
antigüedad real sale de la base, y está **repartida casi uniformemente entre 2018
y 2026**, ~11 % por año. El 78 % tiene más de dos años.

Crece a **~1.000 archivos y 13 GiB al año**.

### Quién escribe y quién lee — el hallazgo que lo cambia todo

**Ningún PHP lee jamás un archivo de REPO.** Cero `readfile`, cero
`file_get_contents`, cero `fopen('r')`. Apache los sirve como estáticos y el
código sólo **construye cadenas de URL**.

| | |
|---|---|
| Escrituras | **34 sentencias**, en `OPENFACTURA/` (26) y `FACTURADOR/` (8) |
| Borrados | **cero** — el corpus es estrictamente *append-only* |
| Lecturas por PHP | **cero** |
| Cadenas de URL | **116 líneas en 60 archivos** |

Y de los diez emisores de DTE que hay en el árbol, **en producción sólo se usa
OPENFACTURA** (1.962 peticiones en 7 días contra cero de los demás).
`FACTURADOR/pdf.php` sigue vivo, pero por `require`, no por HTTP.

### Y una puerta abierta que ya existía

```
GET /mryn/REPO/33_1000000.pdf  ->  200, application/pdf, 100.669 bytes
```

**Cualquiera que alcance el servidor se descarga cualquier DTE sin
autenticarse**, y los folios son secuenciales. No lo introduce la migración: ya
está. Pero la migración es la ocasión barata de cerrarlo.

### El uso real

**3.275 lecturas en 7 días, unas 470 al día** sobre 943.720 archivos. Y de las
lecturas de facturas, el **98,5 % son documentos de los últimos tres meses**. Los
123 GiB están prácticamente fríos.

---

## 4 · El cambio de código para R2

Como nadie lee por PHP, las escrituras son 34 y las lecturas son texto, el cambio
mínimo es pequeño:

**Dos archivos nuevos.** `lib/repo.php` con `repo_put($basename, $bin)` y
`repo_url($basename)`; y `REPO.php`, un endpoint que comprueba la sesión y
responde `302` hacia una **URL prefirmada de R2** con cinco minutos de validez.

**Lado escritura, 34 líneas.** Las 26 de `file_put_contents($sale, $bin)` pasan a
`repo_put(basename($sale), $bin)`. Las 8 de `$pdf->Output('F', ...)` pasan a
`repo_put($nombre, $pdf->Output('S'))` — mPDF y FPDF devuelven el PDF como
cadena con `'S'`.

**Lado lectura, 116 cadenas.** Un `sed`: `'../../REPO/'` → `'../../REPO.php?f='`.

Hay una variante más barata —bucket público y `https://docs.maryun.cl/`— que
cuesta **exactamente las mismas ediciones** y deja los DTE abiertos a todo
internet con folios enumerables. **No se recomienda**: empeora lo que ya está mal.

Como el endpoint redirige en vez de servir, PHP no transporta bytes: sin coste de
memoria ni de ancho de banda.

**Doble escritura las primeras semanas.** `repo_put()` escribe en R2 *y* en disco
local. Es una línea y permite volver atrás sin drama.

### Por qué no `rclone mount` ni `s3fs`

Es tentador porque no toca código. Pero **un directorio plano de 943.720 entradas
sobre S3** significa ~944 llamadas a la API para listar una vez, y cualquier `ls`,
`glob` o pasada de respaldo se vuelve minutos. Y sobre todo: **si la escritura
falla, el folio ya se consumió**. El orden actual es pedir folio al SII y después
escribir el archivo; hoy eso sólo falla si se llena el disco, con un montaje de
red falla también si parpadea la red.

---

## 5 · El destino: cabe con enorme holgura

| recurso | total | en uso | libre |
|---|---|---|---|
| CPU | 24 hilos | ~0,21 núcleos | ~23,8 |
| RAM | 62 GiB | 14 GiB | **47 GiB** |
| `/srv` | 492 GB | 38 GB | **429 GB** |
| LVM sin asignar | — | — | **819 GiB** |

Techos propuestos: **4 CPU y 8 GB** para Apache/PHP, **4 GB** para MariaDB. Un
19 % de la RAM.

**Aviso:** los techos de memoria ya declarados suman 99 GiB sobre 62 GiB físicos
—sobresuscritos un 60 %—. No es un problema hoy, pero `superset-worker` ya va al
91,5 % del suyo.

Va en dos contenedores, `/srv/stacks/mysis/`, siguiendo el patrón de
`planning-saile`: puerto sólo en `10.8.0.1`, secreto en `/srv/secrets`,
`read_only`, healthcheck, techo y logging acotado. Puertos libres: 8096, 8097.

Imagen `php:7.4-apache` con las extensiones que producción tiene y la imagen no:
`gd intl mysqli pdo_mysql xsl zip exif sockets calendar pcntl shmop sysvmsg
sysvsem sysvshm ftp gettext opcache`.

**MariaDB irá más rápido que hoy.** Su `innodb_buffer_pool_size` está en el
**valor por defecto de 128 MB** para 12 GiB de datos: vive de la caché del
sistema. Con 4 GB de buffer pool real, mejora sola.

---

## 6 · Las ataduras, por lo que duelen

**1 · La URL del sistema *es* la IP.** No hay DNS ni TLS. De 34.490 accesos,
**30.343 llegan con `Referer: http://20.153.168.52`**. Hay 19 referencias a esa
IP escritas a mano en código vivo, y ese literal ya salió por correo a
proveedores y por API a terceros.

**2 · Un webhook entrante de Simpliroute apunta a esa IP** y llega de verdad: 98
POST en 14 días, todos 200.

**3 · `HOOKS/orders_events.php` apunta a `45.33.16.82`**, un Linode muerto. Huele
a webhook de Shopify. Si algo externo apunta ahí, ya está roto o lo estará.

**4 · La zona horaria de PHP-CLI no está fijada** y se adivina como
`America/Halifax`. Los cron calculan ventanas de fechas con eso. **En el servidor
nuevo la adivinanza cambia y las fechas se corren en silencio.** Hay que fijar
`America/Santiago` explícitamente y comprobar qué asumía cada cron.

**5 · El juego de caracteres es una trampa.** 163 tablas en `latin1_spanish_ci`,
5 en `utf8mb4`, servidor en `utf8mb4`, cliente negociando `utf8mb3`, y
`mysqli_set_charset($con, "utf8")` **comentado** en `conectar.php`. Funciona hoy
porque las dos puntas coinciden; si el cliente de OVH negocia otra cosa, los
acentos se corrompen. Hay que fijarlo explícitamente **antes** de restaurar.

**6 · `vsftpd` escucha en el puerto 21 abierto al mundo.** Hay que averiguar quién
lo usa antes de no reproducirlo.

**7 · La base entra como `root`** desde `conectar.php`, con la contraseña en texto
plano en ~20 archivos.

Lo que **no** es atadura, y es buena noticia: los 4 usuarios de MariaDB son
`@localhost`, `bind-address` es `127.0.0.1`, las 168 tablas son InnoDB —cero
MyISAM—, y no hay triggers, vistas ni eventos. Un solo procedimiento almacenado.

---

## 7 · El plan

### Fase 0 · Hecho y verificado (18-sep-2026)

- Acceso por `/srv/bin/mysis-ssh.sh`, con la misma clave del túnel.
- Código actualizado y verificado por md5 en los tres puntos.
- rclone instalado en la VM.
- **Los 976.859 archivos están en R2**, y comprobados **por hash**.

| directorio | en disco | en R2 | tamaño |
|---|---|---|---|
| `REPO` | 943.720 | **943.720** | 123,01 GiB |
| `pages/rendicion` | 8.917 | **8.917** | 6,02 GiB |
| `pages/mail` | 100 | **100** | 5,97 GiB |
| `pages/solicitud` | 5.485 | **5.485** | 674,8 MiB |
| `pages/salidas_excel` | 18.637 | **18.637** | 107,5 MiB |

**Subida: 1 h 19 min** (11:57 → 13:16), a ~311 archivos/s sostenidos, **cero
errores**. **Verificación por hash: 13 min 30 s**, con **0 faltantes y 0
diferencias** en los cinco directorios.

Total: **976.859 objetos, ~135,8 GiB**.

Queda dicho para quien repita esto: la verificación por hash lee los 123 GiB
locales y llevó la VM a **carga 32 sobre 4 núcleos**. Un sábado sin nadie está
bien; un lunes no. Para repetirla en horario está `--rapido`, que compara sólo
tamaños.

### Fase 1 · Antes de tocar nada

- Resolver el acceso de `51.222.28.249` y rotar la clave SSH.
- Inventariar los webhooks **entrantes** y quién usa el FTP.
- Decidir el nombre DNS y si MySis se publica o queda tras la VPN.

### Fase 1 · lo que ya está averiguado (18-sep-2026)

**Webhooks entrantes: hay UNO.** Sobre 14 días de registros, el único `POST` que
llega de un tercero es:

```
98  34.122.156.0  ->  /mryn/HOOKS/hook_simpliroute_checkout.php
```

`34.122.156.0` es Google Cloud, que es donde corre Simpliroute. Todo lo demás
que parecía webhook son las llamadas AJAX de las propias pantallas —88.892 a
`autocompletaSKU.php`, 29.977 a `Get_tramo.php`— y un puñado de rastreadores
picando `/api/auth` y `/api/graphql`, que ni siquiera existen en MySis.

**Y los otros hooks están muertos.** `HOOKS/orders_events.php` y `oc_events.php`
—los de Shopify, los que apuntan al Linode difunto `45.33.16.82`— reciben
**cero** peticiones. O Shopify ya no los llama, o los llama a la IP muerta y
nadie se ha enterado. Conviene comprobarlo en el panel de Shopify antes del
corte, no después.

Así que repuntar webhooks es **una sola URL**, no un inventario.

**FTP: prácticamente sin uso.** `vsftpd` tiene `write_enable=YES` y dos
menciones en `syslog`. Antes de no reproducirlo hay que preguntar, pero no
parece sostener nada vivo.

**1.307 IP distintas en 14 días**, contra ~110 usuarios diarios. La diferencia
son rastreadores: es lo que pasa con un servicio publicado en una IP desnuda sin
nombre ni TLS.

### Fase 2 · Montar en paralelo, sin cortar nada

- `/srv/stacks/mysis/` con las dos imágenes, la base restaurada de un dump, y el
  código desplegado. Con `innodb_buffer_pool_size` subido **antes** de restaurar:
  el dump tarda 3 minutos, la restauración 30-60.
- Probar contra una copia. MySis en Azure sigue facturando.

### Fase 3 · El cambio de R2

- `lib/repo.php` y `REPO.php`, las 34 escrituras y las 116 cadenas.
- Doble escritura activada.
- Probar la emisión completa de un DTE en el entorno nuevo.

### Fase 4 · El corte

Fin de semana, fuera de 11:00-22:30 UTC:

1. Detener Apache en Azure —deja de entrar trabajo.
2. `rclone copy` incremental: sólo lo que entró desde la subida masiva.
3. `mysqldump` (3 min) y restaurar.
4. Repuntar el DNS, o repartir la nueva URL.
5. Recrear los 6 cron —el séptimo, `mysql_backup.sh`, **no**: en OVH el respaldo
   entra en el sistema que ya existe.
6. Volver a apuntar el túnel de Mage, que hoy va a Azure.

### Fase 5 · Después, no durante

Subir PHP, actualizar MariaDB 10.6 —**que ya está en fin de vida**— a 10.11 LTS,
y mirar el rendimiento. Nada de esto se mezcla con la mudanza.

---

## 7 bis · La lentitud: medida, con un experimento

Ian dice que «a veces es lento». Se midió el 18-sep-2026 con la máquina libre.
**La mudanza sola arregla la mayor parte, y hay un número que lo demuestra.**

### El experimento

`innodb_buffer_pool_size` es dinámico en MariaDB 10.6, así que se subió de
128 MB a 5 GB **en caliente**, se cronometró y se devolvió, sin reiniciar nada:

| página | con 128 MB | con 5 GB | |
|---|---|---|---|
| `pages/report/cargador.php` | **154-158 s** | **5-8 s** | ~25× |
| `pages/report/datapie.php` | 5,01 s | 0,62 s | 8× |
| `pages/report/vtas2.php` | 5,28 s | 1,01 s | 5× |

### Por qué

El pool son **128 MB para una base de 10,5 GB** —el 1,2 %—, y con
`innodb_flush_method = O_DIRECT` los 14 GB de RAM libre **no ayudan**, porque
InnoDB se salta la caché del sistema operativo. La única caché real son esos
128 MB.

El resultado, medido: **3,85 TB leídos de disco en 27 horas** para servir una
base de 10,5 GB. La base entera se relee del disco unas **385 veces al día**.
Tasa de acierto del pool: 87,3 %, cuando un OLTP sano está por encima del 99,9 %.

### Y qué NO es lento, que importa igual

La primera hipótesis fue «las páginas que tocan tablas grandes van lentas». **Es
falsa, y el escéptico la corrigió midiendo.** La aplicación consulta esas tablas
**por índice**, y eso cuesta 8-15 ms aunque la tabla pese 2,9 GB:

```
mstr_pedidos_aux WHERE pid=…             8,1 / 9,0 / 8,8 ms
tab_clientes_actividades_txt WHERE rut   9,0 / 9,1 / 8,9 ms
tab_clientes_actividad WHERE rut         9,1 / 15,9 / 9,8 ms
```

El eje correcto **no es tabla grande contra tabla pequeña: es agregación contra
búsqueda por índice**. Todo lo transaccional está entre 0,4 ms y 140 ms. Lo único
lento son las páginas de `pages/report/*`, que agregan millones de filas.

Cuando alguien dice «está lento», está hablando de un informe.

### Y algo que es culpa nuestra

Un cliente ODBC por el usuario `appread` —o sea **nuestro propio ETL de Mage, por
el túnel**— lanza cada 4-5 minutos un informe de 51 tablas con 10 subconsultas
correlacionadas que tarda **105 segundos** y devuelve 113 filas. Satura el disco
al 88-94 % durante 2 min 23 s, unas 156 veces al día, **las 24 horas, incluso a
las 3 de la madrugada**.

Eso es lo que produce la mayor parte de esos 3,85 TB. Es nuestro, y se puede
arreglar sin tocar MySis: o bajar la frecuencia, o arreglar la consulta.

### Lo que NO explica la lentitud, descartado con dato

- **No es PHP.** Cero «Maximum execution time exceeded», cero «Allowed memory
  size exhausted» en dos días de registro. OPcache activo y sano.
- **No es la CPU.** Nunca pasa del 5 % de usuario.
- **No es MyISAM.** No hay ni una tabla MyISAM.
- **No son los 943.720 archivos.** Ningún código los lista.
- **No es bloqueo.** Cero esperas de fila, cero semáforos, `History list` en 0.
  Es 100 % lectura: 0,00 escrituras/s.

### Lo que queda por hacer aunque se mude

`cargador.php` pasa de 154 s a 5-8 s con la mudanza. **Pero 5-8 segundos para una
página sigue siendo un fallo de consulta, no de hierro.** Merece un `EXPLAIN`
antes de darlo por resuelto. Es fase 5.

---

## 7 ter · Una segunda puerta abierta, peor que la de REPO

Al cronometrar las páginas salió esto, y no se buscaba:

**El control de sesión de MySis es sólo un redirect de JavaScript en el cliente.**
Las páginas devuelven

```html
<script>window.location.href="/mryn/pages/login.php";</script>
```

**y a continuación ejecutan el PHP y entregan los datos igual.** Comprobado sin
sesión alguna: `skuget.php` devolvió `{"count":"19211"}` y `caja.php` devolvió
70 KB de HTML con el título «VENTAS | FACTURACION».

Cualquiera que alcance el servidor y ignore el JavaScript lee los datos. Y hay
**1.307 IP distintas** llegando en 14 días contra ~110 usuarios reales.

Esto **decide** la pregunta de si MySis se publica o queda tras la VPN: **tras la
VPN**, hasta que la autenticación se compruebe en el servidor. Arreglarla de
verdad es un `require` de guardia al principio de cada página, y es un trabajo
aparte — pero mientras tanto, la red es lo único que separa los datos de
cualquiera.

---

## 8 · Lo que puede salir mal

**Perder documentos tributarios.** ~222.900 PDF anteriores a 2022 no tienen token
en `mstr_pedidos_token` y su XML firmado no está ni en la base ni en disco. **Para
ese 27 % del corpus, el archivo de REPO es la única copia que posee Maryun**, y el
SII puede requerirlos durante seis años. No se borra nada del origen hasta
verificar objeto a objeto, y conviene además una copia fría fuera de R2.

**La facturación se detiene si falla la escritura.** El folio se pide al SII
*antes* de escribir el archivo. Un fallo de R2 deja un folio emitido sin
documento. Por eso la doble escritura, y por eso un fallo de R2 debe registrar y
reintentar, nunca abortar la emisión.

**30 basenames duplicados.** El espacio plano ya ha sobrescrito unos 30
documentos históricamente. Se replica en R2. Conviene listarlos y decidir.

**El directorio plano es una trampa operativa.** Un `find` sobre él ya produjo
11-23 % de iowait. Usar siempre `--files-from` y `--no-traverse`.

---

## 9 · Lo que cuesta

| | |
|---|---|
| Salida de 139 GB desde Azure | ~$12 una vez |
| 943.720 escrituras en R2 | ~$4,25 una vez |
| Almacenamiento, 123 GiB | **~$1,90 al mes** |
| Salida desde R2 | **gratis** |

Medido: **67 MB/s de subida** desde Azure a Cloudflare, y **311 archivos/s** con
rclone a 64 transferencias en paralelo.
