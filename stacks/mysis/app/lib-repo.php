<?php
/**
 * Acceso a los archivos de DTE, que ahora viven en R2 y no en el disco.
 *
 * DESTINO EN EL ÁRBOL DE MYSIS:  /var/www/html/mryn/lib/repo.php
 *
 * ── Por qué esto es tan corto ────────────────────────────────────────────────
 *
 * Porque el relevamiento del 18-sep-2026 encontró que NINGÚN PHP lee jamás un
 * archivo de REPO. Ni un readfile(), ni un file_get_contents(), ni un fopen().
 * Apache los sirve como estáticos y el código sólo construye cadenas de URL.
 *
 * Así que todo el trato con los 943.720 archivos son 34 sentencias de escritura
 * -26 file_put_contents() en OPENFACTURA/ y 8 Output('F') en FACTURADOR/- y 116
 * cadenas de texto repartidas en 60 archivos. Eso es lo que estas dos funciones
 * sustituyen.
 *
 * ── Por qué la clave del objeto es el nombre de hoy ──────────────────────────
 *
 * REPO es un único directorio PLANO y los nombres son {tipoDTE}_{folio}.{ext} en
 * el 99,999 % de los casos. Y `mstr_pedidos.factura_pdf` ya guarda exactamente
 * ese basename. O sea que la correspondencia entre lo que hay en la base y la
 * clave en R2 es la identidad: no hay que tocar el esquema, ni migrar datos, ni
 * mantener una tabla de equivalencias que se pueda desincronizar.
 *
 * ── Por qué se firma a mano y no con el SDK de AWS ───────────────────────────
 *
 * aws-sdk-php v3 funcionaría en PHP 7.4, pero son ~15 MB de dependencia con su
 * propio árbol de vendor dentro de un PHP que lleva sin soporte desde 2022.
 * Firmar SigV4 para GET y PUT son las setenta líneas de abajo, sin dependencias,
 * y se lee entero de una sentada. Cuando se suba la versión de PHP (fase 5) se
 * puede reconsiderar.
 */

// ── Configuración ───────────────────────────────────────────────────────────
//
// Las credenciales entran por el entorno, nunca escritas aquí. En el contenedor
// llegan desde /srv/secrets/mysis-r2.env por `env_file`.
define('REPO_R2_ENDPOINT', getenv('MYSIS_R2_ENDPOINT') ?: '');
define('REPO_R2_BUCKET',   getenv('MYSIS_R2_BUCKET')   ?: '');
define('REPO_R2_KEY',      getenv('MYSIS_R2_KEY_ID')   ?: '');
define('REPO_R2_SECRET',   getenv('MYSIS_R2_SECRET')   ?: '');

// R2 no tiene regiones: siempre `auto`. Firmar con otra cosa da 403.
define('REPO_R2_REGION', 'auto');

/**
 * Durante las primeras semanas se escribe en R2 **y** en disco.
 *
 * No es desconfianza, es el orden de la emisión: MySis pide el folio al SII a
 * través de Haulmer y DESPUÉS escribe el archivo. Si la escritura falla, el
 * folio ya se consumió y queda un documento tributario emitido sin PDF. Hoy eso
 * sólo pasa si se llena el disco; con R2 pasa también si parpadea la red.
 *
 * Con la doble escritura, un fallo de R2 no pierde nada y se puede volver atrás
 * cambiando una constante. Se apaga cuando haya semanas de evidencia.
 */
define('REPO_DOBLE_ESCRITURA', true);
define('REPO_DIR_LOCAL', __DIR__ . '/../REPO');

/** Cuánto vive el enlace firmado que se le da al navegador. */
define('REPO_URL_SEGUNDOS', 300);


/**
 * Guarda un archivo del repositorio de DTE.
 *
 * @param string $nombre  El basename de siempre: "33_1047488.pdf".
 * @param string $bin     El contenido.
 * @return bool           true si quedó guardado en algún sitio.
 *
 * NO LANZA EXCEPCIÓN SI R2 FALLA, y es deliberado: esta función se llama con un
 * folio del SII ya consumido. Abortar la emisión por un fallo de red sería
 * cambiar un problema recuperable -un archivo que falta y se puede resubir- por
 * uno que no lo es. Registra y sigue.
 */
function repo_put($nombre, $bin)
{
    $nombre = basename($nombre);
    $ok_local = false;
    $ok_r2 = false;

    if (REPO_DOBLE_ESCRITURA) {
        $ok_local = @file_put_contents(REPO_DIR_LOCAL . '/' . $nombre, $bin) !== false;
        if (!$ok_local) {
            error_log("repo_put: no pude escribir en disco $nombre");
        }
    }

    try {
        $ok_r2 = repo_r2_put($nombre, $bin);
    } catch (Exception $e) {
        error_log('repo_put: R2 falló para ' . $nombre . ': ' . $e->getMessage());
        $ok_r2 = false;
    }

    if (!$ok_r2) {
        // Que quede rastro de qué hay que resubir. Un archivo de texto y no la
        // base a propósito: si lo que falla es la red, la base puede estar
        // igual de lejos.
        @file_put_contents('/tmp/repo-pendientes.txt', $nombre . "\n", FILE_APPEND);
    }

    return $ok_r2 || $ok_local;
}


/**
 * La URL que se le da al navegador para ver un documento.
 *
 * Devuelve la ruta al endpoint que comprueba la sesión, NO la URL de R2. El
 * endpoint redirige a un enlace firmado de cinco minutos.
 *
 * Se podría devolver directamente una URL pública de R2 y ahorrarse el salto —
 * cuesta exactamente las mismas ediciones. No se hace porque hoy los DTE ya se
 * descargan sin autenticarse con folios secuenciales, y un bucket público
 * convertiría ese agujero de «quien alcance el servidor» en «todo internet».
 *
 * @param string $nombre  "33_1047488.pdf"
 * @param string $base    Prefijo relativo desde la página que llama, p. ej. '../../'
 */
function repo_url($nombre, $base = '../../')
{
    return $base . 'REPO.php?f=' . rawurlencode(basename($nombre));
}


// ── Firma SigV4, lo único con enjundia de este archivo ──────────────────────

/** Sube un objeto con PUT firmado. */
function repo_r2_put($clave, $bin)
{
    $host = parse_url(REPO_R2_ENDPOINT, PHP_URL_HOST);
    $ruta = '/' . REPO_R2_BUCKET . '/' . $clave;
    $ahora = gmdate('Ymd\THis\Z');
    $dia = substr($ahora, 0, 8);
    $sha = hash('sha256', $bin);

    $canon = "PUT\n{$ruta}\n\n"
           . "host:{$host}\nx-amz-content-sha256:{$sha}\nx-amz-date:{$ahora}\n\n"
           . "host;x-amz-content-sha256;x-amz-date\n{$sha}";

    $alcance = "{$dia}/" . REPO_R2_REGION . "/s3/aws4_request";
    $firmar = "AWS4-HMAC-SHA256\n{$ahora}\n{$alcance}\n" . hash('sha256', $canon);
    $firma = hash_hmac('sha256', $firmar, repo_clave_firma($dia));

    $cab = [
        'Host: ' . $host,
        'x-amz-content-sha256: ' . $sha,
        'x-amz-date: ' . $ahora,
        'Content-Type: ' . repo_tipo($clave),
        'Authorization: AWS4-HMAC-SHA256 Credential=' . REPO_R2_KEY . '/' . $alcance
            . ', SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature=' . $firma,
    ];

    $ch = curl_init(REPO_R2_ENDPOINT . $ruta);
    curl_setopt_array($ch, [
        CURLOPT_CUSTOMREQUEST  => 'PUT',
        CURLOPT_POSTFIELDS     => $bin,
        CURLOPT_HTTPHEADER     => $cab,
        CURLOPT_RETURNTRANSFER => true,
        // Cortos a propósito: esto corre dentro de una emisión de DTE y no
        // puede colgarse esperando. Si no responde, se anota como pendiente.
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_TIMEOUT        => 30,
    ]);
    $resp = curl_exec($ch);
    $cod = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);

    if ($cod < 200 || $cod >= 300) {
        throw new Exception("HTTP $cod " . ($err ?: substr((string) $resp, 0, 200)));
    }
    return true;
}


/** Devuelve una URL GET prefirmada, que el navegador puede seguir sin credenciales. */
function repo_r2_url_firmada($clave, $segundos = REPO_URL_SEGUNDOS)
{
    $host = parse_url(REPO_R2_ENDPOINT, PHP_URL_HOST);
    $ruta = '/' . REPO_R2_BUCKET . '/' . rawurlencode($clave);
    $ahora = gmdate('Ymd\THis\Z');
    $dia = substr($ahora, 0, 8);
    $alcance = "{$dia}/" . REPO_R2_REGION . "/s3/aws4_request";

    $q = [
        'X-Amz-Algorithm'     => 'AWS4-HMAC-SHA256',
        'X-Amz-Credential'    => REPO_R2_KEY . '/' . $alcance,
        'X-Amz-Date'          => $ahora,
        'X-Amz-Expires'       => (string) $segundos,
        'X-Amz-SignedHeaders' => 'host',
    ];
    ksort($q);
    $cadena = http_build_query($q, '', '&', PHP_QUERY_RFC3986);

    // UNSIGNED-PAYLOAD porque en un GET prefirmado no hay cuerpo que firmar.
    $canon = "GET\n{$ruta}\n{$cadena}\nhost:{$host}\n\nhost\nUNSIGNED-PAYLOAD";
    $firmar = "AWS4-HMAC-SHA256\n{$ahora}\n{$alcance}\n" . hash('sha256', $canon);
    $firma = hash_hmac('sha256', $firmar, repo_clave_firma($dia));

    return REPO_R2_ENDPOINT . $ruta . '?' . $cadena . '&X-Amz-Signature=' . $firma;
}


/** La cadena de derivación de SigV4: clave -> día -> región -> servicio. */
function repo_clave_firma($dia)
{
    $k = hash_hmac('sha256', $dia, 'AWS4' . REPO_R2_SECRET, true);
    $k = hash_hmac('sha256', REPO_R2_REGION, $k, true);
    $k = hash_hmac('sha256', 's3', $k, true);
    return hash_hmac('sha256', 'aws4_request', $k, true);
}


/**
 * El tipo de contenido, por extensión.
 *
 * Importa: si se sube un PDF sin `Content-Type`, R2 lo guarda como
 * `application/octet-stream` y el navegador lo descarga en vez de mostrarlo.
 * Hoy Apache lo deduce solo, así que es una de las pocas cosas que hay que
 * reponer a mano al cambiar de servidor de archivos.
 */
function repo_tipo($nombre)
{
    $ext = strtolower(pathinfo($nombre, PATHINFO_EXTENSION));
    if ($ext === 'pdf') return 'application/pdf';
    if ($ext === 'png') return 'image/png';
    if ($ext === 'xml') return 'application/xml';
    return 'application/octet-stream';
}
