#!/usr/bin/env python3
"""Apunta los conectar*.php del arbol desplegado a la base de OVH.

    sudo /srv/stacks/mysis/repuntar-conectar.py            dice que cambiaria
    sudo /srv/stacks/mysis/repuntar-conectar.py --hazlo    lo cambia

SOLO TOCA /srv/stacks/mysis/html. Nunca el arbol de Azure, que sigue siendo
produccion.

QUE CAMBIA, y nada mas:
  $servername = "127.0.0.1"   ->   "mysis-db"
  $password   = "<la de Azure>" -> la nueva, de /srv/secrets/mysis-db.env

QUE NO TOCA:
  - Las lineas COMENTADAS. Hay bloques con otro host y otra credencial
    (maryun.backupcode.net) comentados desde hace anios; borrarlos o
    reescribirlos seria decidir por alguien que no esta, y no hace falta para
    que esto funcione.
  - El usuario, que sigue siendo root. Cambiarlo a un usuario propio de la
    aplicacion es lo correcto y es lo PRIMERO de la fase 5, pero mezclarlo con
    la mudanza significa que si algo falla no se sabe cual de las dos cosas fue.

Deja un respaldo por archivo en /srv/stacks/mysis/respaldos-conectar, FUERA
del arbol que sirve Apache. La primera version los dejaba al lado, como
conectar.php.bak-ovh, y eso resulto ser un agujero: Apache no ejecuta un
archivo que no termine en .php, asi que lo servia EN CLARO. Comprobado -HTTP
200, 750 bytes, con la credencial dentro-. Un respaldo de un archivo de
conexion no puede vivir donde lo alcanza un navegador.

Idempotente: correrlo dos veces no hace nada la segunda.
"""
import os
import re
import sys

RAIZ = '/srv/stacks/mysis/html'
ENV = '/srv/secrets/mysis-db.env'
RESPALDOS = '/srv/stacks/mysis/respaldos-conectar'
HAZLO = '--hazlo' in sys.argv

clave = None
with open(ENV, encoding='utf-8') as f:
    for linea in f:
        if linea.startswith('MARIADB_ROOT_PASSWORD='):
            clave = linea.split('=', 1)[1].strip()
if not clave:
    raise SystemExit('no encontre MARIADB_ROOT_PASSWORD en ' + ENV)

# Sin comentar: la linea no empieza por // ni por # ni por /*
ACTIVA = re.compile(r'^(?!\s*(//|#|\*|/\*))\s*\$(servername|password)\s*=\s*"[^"]*"\s*;', re.M)

cambiados = 0
tocados = []
sin_cambios = 0

for base, _, archivos in os.walk(RAIZ):
    for nombre in archivos:
        if not (nombre.startswith('conectar') and nombre.endswith('.php')):
            continue
        ruta = os.path.join(base, nombre)
        try:
            with open(ruta, encoding='utf-8', errors='surrogateescape') as f:
                original = f.read()
        except OSError as e:
            print('  no pude leer %s: %s' % (ruta, e))
            continue

        def sustituir(m):
            entero = m.group(0)
            if '$servername' in entero:
                return re.sub(r'"[^"]*"', '"mysis-db"', entero, count=1)
            return re.sub(r'"[^"]*"', '"%s"' % clave, entero, count=1)

        nuevo = ACTIVA.sub(sustituir, original)

        if nuevo == original:
            sin_cambios += 1
            continue

        rel = ruta[len(RAIZ) + 1:]
        tocados.append(rel)
        cambiados += 1
        if HAZLO:
            os.makedirs(RESPALDOS, exist_ok=True)
            respaldo = os.path.join(RESPALDOS, rel.replace(os.sep, '_'))
            if not os.path.exists(respaldo):
                with open(respaldo, 'w', encoding='utf-8', errors='surrogateescape') as f:
                    f.write(original)
                os.chmod(respaldo, 0o600)
            with open(ruta, 'w', encoding='utf-8', errors='surrogateescape') as f:
                f.write(nuevo)

print('archivos que %s: %d' % ('se cambiaron' if HAZLO else 'se cambiarian', cambiados))
print('archivos ya al dia o sin asignacion activa: %d' % sin_cambios)
for t in sorted(tocados):
    print('   ' + t)
if not HAZLO:
    print('\nsimulacion. con --hazlo se aplica.')
