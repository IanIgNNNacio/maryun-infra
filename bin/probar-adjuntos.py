#!/usr/bin/env python3
"""Prueba de subida de adjuntos del ERP: facturas y nominas.

Que se prueba de verdad
-----------------------
El camino de almacenamiento completo: credenciales de R2, la forma exacta de la
clave que construye buildKey(), las filas de InvoiceAttachment, y que el
respaldo de adjuntos a maryun-erp-respaldo/adjuntos los recoja.

Que NO se prueba
----------------
La ruta HTTP /api/adjuntos ni sus permisos. Esas rutas exigen sesion de
NextAuth (Microsoft) y no hay token de API: simularlas obligaria a forjar una
sesion, y eso no se hace. Tampoco se ejecuta el codigo TypeScript del driver:
el contenedor es una compilacion de produccion sin tsx ni node_modules de
desarrollo. Aqui se replica su logica -sanitize, buildKey, checksum sha256- y
se sube con rclone, que firma SigV4 igual que el driver.

Seguridad
---------
Solo se tocan documentos de COMPRA: son facturas que el SII ya listo en el RCV,
recibidas, sin nada que emitir. Adjuntar un archivo es una operacion interna y
no toca al SII: no escribe fechaAcuseRecibo ni dispara ningun envio.

Las nominas se crean en BORRADOR, con numero prefijado PRUEBA-ADJ para que se
distingan y se puedan borrar.

    probar-adjuntos.py            informa que haria
    probar-adjuntos.py --hazlo    ejecuta
    probar-adjuntos.py --limpiar  borra lo que creo esta prueba
"""
import base64
import hashlib
import os
import re
import subprocess
import sys
import unicodedata
import uuid
from datetime import datetime, timezone, timedelta

HAZLO = "--hazlo" in sys.argv
LIMPIAR = "--limpiar" in sys.argv
CONT_DB = "maryun-erp-db"
BASE = "maryun_erp"
PREFIJO_NOMINA = "PRUEBA-ADJ"
TRABAJO = "/tmp/prueba-adjuntos"
SANTIAGO = timezone(timedelta(hours=-4))


def psql(sql, base=BASE):
    r = subprocess.run(
        ["docker", "exec", "-i", CONT_DB, "psql", "-U", "maryun", "-d", base,
         "-X", "-tA", "-F", "\t", "-v", "ON_ERROR_STOP=1", "-f", "-"],
        input=sql, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:600])
    return r.stdout


def sanitize(nombre):
    """Copia fiel de sanitize() en lib/storage/driver.ts."""
    s = unicodedata.normalize("NFD", nombre)
    s = re.sub(r"[^a-zA-Z0-9._-]", "_", s)
    s = re.sub(r"_{2,}", "_", s)
    s = re.sub(r"^[._-]+", "", s)
    return s[:120] or "archivo"


ESPACIO = "adjuntos"


def build_key(scope, oid, filename, cuando):
    """Copia fiel de buildKey() en lib/storage/driver.ts.

    OJO con el prefijo del espacio: buildKey NO lo incluye. Lo pone conPrefijo()
    dentro de getStorage(), asi que el objeto en el bucket vive en
    "adjuntos/<scope>/..." pero InvoiceAttachment.storageKey guarda solo
    "<scope>/...". Guardarlo con el prefijo hace que la aplicacion busque
    "adjuntos/adjuntos/<scope>/..." al abrir el archivo, y el visor falla.
    """
    yyyymm = "%d/%02d" % (cuando.year, cuando.month)
    return "%s/%s/%s/%s" % (scope, yyyymm, oid, sanitize(filename))


def ruta_en_bucket(clave):
    """Donde vive de verdad el objeto: la clave con el prefijo del espacio."""
    return "%s/%s" % (ESPACIO, clave)


# ─────────────────────────────────────────────────────────────── limpieza ──
if LIMPIAR:
    filas = psql("""
        SELECT a.id, a."storageKey"
        FROM "InvoiceAttachment" a
        LEFT JOIN "Nomina" n ON n.id = a."nominaId"
        WHERE a."uploadedBy" = 'prueba-adjuntos'
           OR n.number LIKE '%s%%';
    """ % PREFIJO_NOMINA).strip()
    claves = [l.split("\t")[1] for l in filas.splitlines() if l.strip()]
    print("adjuntos a borrar: %d" % len(claves))
    for c in claves:
        print("   %s" % c)
    if not HAZLO:
        print("\n(informativo; agrega --hazlo para borrar)")
        sys.exit(0)
    psql("""
        DELETE FROM "InvoiceAttachment"
        WHERE "uploadedBy" = 'prueba-adjuntos'
           OR "nominaId" IN (SELECT id FROM "Nomina" WHERE number LIKE '%s%%');
        DELETE FROM "Nomina" WHERE number LIKE '%s%%';
    """ % (PREFIJO_NOMINA, PREFIJO_NOMINA))
    print("filas borradas. Los objetos de R2 hay que borrarlos aparte:")
    for c in claves:
        print("   rclone delete origen:maryun-erp/%s/%s" % (ESPACIO, c))
    sys.exit(0)


# ─────────────────────────────────────── que documentos y nominas se usan ──
docs = [l.split("\t") for l in psql("""
    SELECT id, "tipoDoc", "folioNorm", "rutNorm", coalesce("nombreContraparte",'-')
    FROM "SiiDocument"
    WHERE operacion = 'COMPRA'
    ORDER BY "fechaEmision" DESC NULLS LAST
    LIMIT 2;
""").strip().splitlines() if l.strip()]

if len(docs) < 2:
    print("no hay suficientes documentos de COMPRA para la prueba")
    sys.exit(1)

print("== documentos de COMPRA elegidos (recibidos: nada que emitir al SII)")
for d in docs:
    print("   tipo %s folio %s  %s  %s" % (d[1], d[2], d[3], d[4][:34]))

print("")
print("== nominas de prueba que se crean (BORRADOR)")
nominas = [("%s-%d" % (PREFIJO_NOMINA, i), "Nomina de prueba de adjuntos %d" % i)
           for i in (1, 2)]
for n in nominas:
    print("   %s  %s" % n)

# Dos archivos: un PDF y un PNG. El PDF va con acento en el nombre a proposito,
# para ver que hace sanitize() con el.
def pdf_minimo():
    """Un PDF valido de verdad, con tabla xref y desplazamientos correctos.

    La primera version de esta prueba escribia el PDF a mano, sin xref ni
    startxref. Empezaba por %PDF-1.4 y `file` lo daba por bueno, pero ningun
    visor lo abre: Chrome mostraba el icono de archivo roto y parecia un fallo
    del ERP cuando el problema era el archivo de prueba. Los desplazamientos se
    calculan aqui para que no puedan quedar mal.
    """
    contenido = b"BT /F1 14 Tf 20 60 Td (Adjunto de prueba - maryun ERP) Tj ET\n"
    objetos = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 320 120]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(contenido)).encode() + b">>stream\n"
        + contenido + b"endstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    salida = bytearray(b"%PDF-1.4\n")
    desplazamientos = []
    for i, o in enumerate(objetos, start=1):
        desplazamientos.append(len(salida))
        salida += str(i).encode() + b" 0 obj" + o + b"\nendobj\n"
    inicio_xref = len(salida)
    salida += b"xref\n0 " + str(len(objetos) + 1).encode() + b"\n"
    salida += b"0000000000 65535 f \n"
    for d in desplazamientos:
        salida += ("%010d 00000 n \n" % d).encode()
    salida += (b"trailer<</Size " + str(len(objetos) + 1).encode()
               + b"/Root 1 0 R>>\nstartxref\n" + str(inicio_xref).encode()
               + b"\n%%EOF\n")
    return bytes(salida)


PDF = pdf_minimo()
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAKklEQVR42mNkYPhfz0BBYBxV"
    "MKpgVMGoglEFowpGFYwqGFUwqmBUAQMDAJb+D/HrDh2zAAAAAElFTkSuQmCC")

ARCHIVOS = [
    ("Cotizacio\u0301n de prueba.pdf", "application/pdf", PDF),
    ("respaldo prueba.png", "image/png", PNG),
]

tareas = []
ahora = datetime.now(SANTIAGO)
for d in docs:
    nombre, ct, cuerpo = ARCHIVOS[0]
    tareas.append(dict(scope="facturas", owner="documentId", oid=d[0],
                       rut=d[3], tipo=int(d[1]), folio=d[2],
                       filename=nombre, ct=ct, cuerpo=cuerpo))
for i, (num, _) in enumerate(nominas):
    nombre, ct, cuerpo = ARCHIVOS[i % 2]
    tareas.append(dict(scope="nominas", owner="nominaId", oid=None, numero=num,
                       rut="", tipo=0, folio="",
                       filename=nombre, ct=ct, cuerpo=cuerpo))

print("")
print("== %d adjuntos a subir" % len(tareas))
if not HAZLO:
    for t in tareas:
        chk = hashlib.sha256(t["cuerpo"]).hexdigest()
        oid = t["oid"] or "<id de la nomina>"
        print("   %s" % ruta_en_bucket(build_key(t["scope"], oid, "%s-%s" % (chk[:8], t["filename"]), ahora)))
    print("")
    print("(informativo; agrega --hazlo para ejecutar)")
    sys.exit(0)

# ──────────────────────────────────────────────── crear las nominas antes ──
# id y updatedAt los pone Prisma en el cliente, no la base: en un INSERT
# directo hay que darlos explicitos o el INSERT falla por NOT NULL.
for num, nom in nominas:
    nid = str(uuid.uuid4())
    psql("""
        INSERT INTO "Nomina" (id, number, name, type, status, "paidOnPlatform",
                              "createdBy", "createdAt", "updatedAt")
        VALUES ('%s', '%s', '%s', 'NORMAL', 'BORRADOR', false,
                'prueba-adjuntos', now(), now())
        ON CONFLICT (number) DO NOTHING;
    """ % (nid, num, nom))
real = {}
for num, _ in nominas:
    real[num] = psql("SELECT id FROM \"Nomina\" WHERE number = '%s';" % num).strip()
    print("   nomina %s -> %s" % (num, real[num]))
for t in tareas:
    if t["owner"] == "nominaId":
        t["oid"] = real[t["numero"]]

# ─────────────────────────────────────────────────────── subir y registrar ──
os.makedirs(TRABAJO, exist_ok=True)
env = {}
for linea in open("/srv/secrets/r2-respaldo.env", encoding="utf-8"):
    if "=" in linea and not linea.startswith("#"):
        k, v = linea.strip().split("=", 1)
        env[k] = v

print("")
print("== subiendo a R2 (bucket %s)" % env["R2_ORIGEN_BUCKET"])
for t in tareas:
    chk = hashlib.sha256(t["cuerpo"]).hexdigest()
    clave = build_key(t["scope"], t["oid"], "%s-%s" % (chk[:8], t["filename"]), ahora)
    destino = ruta_en_bucket(clave)
    local = os.path.join(TRABAJO, os.path.basename(clave))
    with open(local, "wb") as f:
        f.write(t["cuerpo"])

    r = subprocess.run([
        "docker", "run", "--rm", "-v", "%s:/datos" % TRABAJO,
        "-e", "RCLONE_CONFIG_O_TYPE=s3",
        "-e", "RCLONE_CONFIG_O_PROVIDER=Cloudflare",
        "-e", "RCLONE_CONFIG_O_ENDPOINT=" + env["R2_ENDPOINT"],
        "-e", "RCLONE_CONFIG_O_ACCESS_KEY_ID=" + env["R2_ORIGEN_KEY_ID"],
        "-e", "RCLONE_CONFIG_O_SECRET_ACCESS_KEY=" + env["R2_ORIGEN_SECRET"],
        "rclone/rclone:latest", "copyto",
        "/datos/" + os.path.basename(clave),
        "o:%s/%s" % (env["R2_ORIGEN_BUCKET"], destino),
        "--s3-no-check-bucket",
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print("   FALLO %s: %s" % (destino, (r.stderr or r.stdout)[-300:]))
        continue

    psql("""
        INSERT INTO "InvoiceAttachment"
          (id, "%s", "rutNorm", "tipoDoc", "folioNorm", filename, "contentType",
           size, "storageKey", driver, source, checksum, "uploadedBy", "createdAt")
        VALUES ('%s', '%s', '%s', %d, '%s', '%s', '%s',
                %d, '%s', 'r2', 'MANUAL', '%s', 'prueba-adjuntos', now())
        ON CONFLICT ("storageKey") DO NOTHING;
    """ % (t["owner"], str(uuid.uuid4()), t["oid"], t["rut"], t["tipo"], t["folio"],
           t["filename"].replace("'", "''"), t["ct"], len(t["cuerpo"]), clave, chk))
    print("   ok  %-9s %6d B  %s" % (t["scope"], len(t["cuerpo"]), destino))

print("")
print("== filas de InvoiceAttachment creadas")
print(psql("""
    SELECT coalesce(n.number, 'factura tipo '||a."tipoDoc"||' folio '||a."folioNorm"),
           a.filename, a.size, a.driver, left(a.checksum, 8)
    FROM "InvoiceAttachment" a
    LEFT JOIN "Nomina" n ON n.id = a."nominaId"
    WHERE a."uploadedBy" = 'prueba-adjuntos'
    ORDER BY a."createdAt";
"""))
