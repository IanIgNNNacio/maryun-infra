<?php
/**
 * La puerta a los documentos de DTE.
 *
 * DESTINO EN EL ÁRBOL DE MYSIS:  /var/www/html/mryn/REPO.php
 *
 * Reemplaza a `/mryn/REPO/33_1047488.pdf`, que hoy sirve Apache como estático.
 *
 * ── Qué arregla, además de mover los archivos ───────────────────────────────
 *
 * Hoy esto responde 200 a cualquiera que alcance el servidor:
 *
 *     GET /mryn/REPO/33_1000000.pdf   ->   200, application/pdf, 100.669 bytes
 *
 * Sin sesión, sin cabecera, sin nada. Y los folios son secuenciales: quien tenga
 * uno los tiene todos. No es un problema que introduzca la migración —lleva años
 * así— pero es la ocasión de cerrarlo sin coste: pasar por aquí cuesta las mismas
 * ediciones que apuntar a un bucket público, y un bucket público convertiría
 * «quien alcance el servidor» en «todo internet».
 *
 * ── Por qué redirige en vez de servir ───────────────────────────────────────
 *
 * Porque así PHP no transporta un solo byte del documento. Comprueba la sesión,
 * firma un enlace de cinco minutos y manda al navegador a buscarlo directo a R2.
 * Sin coste de memoria, sin coste de ancho de banda, y sin que un PDF de 800 KB
 * pase por un proceso de Apache. Es también lo que hace que funcione igual de
 * bien con 470 lecturas al día que con 47.000.
 */

require_once __DIR__ . '/lib/repo.php';

session_start();

/**
 * La guardia.
 *
 * `mitoken` es lo que `pages/login.php` deja puesto al entrar; es lo mismo que
 * miran el resto de las pantallas. No se inventa un mecanismo nuevo: si alguna
 * vez cambia la forma de comprobar la sesión, que cambie en un sitio y aquí se
 * siga lo que haga el resto.
 */
if (empty($_SESSION['mitoken'])) {
    header('HTTP/1.1 403 Forbidden');
    header('Content-Type: text/plain; charset=utf-8');
    echo "Hay que iniciar sesión para ver este documento.\n";
    exit;
}

$f = isset($_GET['f']) ? (string) $_GET['f'] : '';

/**
 * Validación del nombre, y es estricta a propósito.
 *
 * El 99,999 % de los 943.720 archivos encaja en {tipo}_{folio}.{ext}. Aceptar
 * sólo eso cierra de un golpe cualquier intento de salirse del repositorio
 * —`../`, rutas absolutas, bytes nulos— sin tener que razonar sobre
 * normalización de rutas, que es donde se cuelan los errores.
 *
 * Los ~26 archivos con nombre anómalo (folio vacío, doble folio, tres sueltos)
 * quedan fuera. Están identificados y son de 2020 o anteriores; si alguno hiciera
 * falta, se trata como excepción y no ensanchando esta expresión.
 */
if (!preg_match('/^[0-9]{1,4}_[0-9]{1,12}\.(pdf|PNG|png|xml)$/', $f)) {
    header('HTTP/1.1 400 Bad Request');
    header('Content-Type: text/plain; charset=utf-8');
    echo "Nombre de documento no válido.\n";
    exit;
}

/**
 * Durante la doble escritura, si el archivo está en disco se sirve de disco.
 *
 * No es por velocidad: es el repliegue. Mientras REPO_DOBLE_ESCRITURA esté
 * encendida hay dos copias, y que esta página prefiera la local significa que un
 * problema en R2 no deja a nadie sin poder abrir una factura. Cuando se apague
 * la doble escritura, esta rama deja de encontrar nada y todo pasa por R2 sola.
 */
if (REPO_DOBLE_ESCRITURA) {
    $local = REPO_DIR_LOCAL . '/' . $f;
    if (is_file($local)) {
        header('Content-Type: ' . repo_tipo($f));
        header('Content-Length: ' . filesize($local));
        // `inline` y no `attachment`: hoy Apache los muestra en el navegador y
        // la gente está acostumbrada a eso. Cambiarlo sería un cambio de
        // comportamiento colado dentro de una migración.
        header('Content-Disposition: inline; filename="' . $f . '"');
        readfile($local);
        exit;
    }
}

/**
 * Y si no, a R2.
 *
 * 302 y no 301: el enlace firmado caduca en cinco minutos, así que no se puede
 * permitir que ningún intermediario lo guarde como permanente.
 */
header('Cache-Control: private, no-store');
header('Location: ' . repo_r2_url_firmada($f), true, 302);
exit;
