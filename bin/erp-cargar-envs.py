#!/usr/bin/env python3
"""Carga variables de entorno en las apps del ERP en Coolify desde un archivo.

Uso:
    erp-cargar-envs.py produccion /ruta/al/archivo.env
    erp-cargar-envs.py preview    /ruta/al/archivo.env

El archivo es formato KEY=valor, una por linea. Las lineas que empiezan con #
se ignoran. Si la variable ya existe se actualiza; si no, se crea.
Nunca imprime valores: solo nombres.
"""
import json
import sys
import urllib.error
import urllib.request

APPS = {
    'produccion': 'n5hirwwi5dytybx4pizafony',
    'preview': 'beij8pzyjehnufadehhvheqk',
}

if len(sys.argv) < 3 or sys.argv[1] not in APPS:
    print(__doc__)
    raise SystemExit(1)

app_uuid = APPS[sys.argv[1]]
archivo = sys.argv[2]

cfg = {}
with open('/srv/secrets/coolify-api.env') as f:
    for l in f:
        l = l.strip()
        if l and not l.startswith('#') and '=' in l:
            k, v = l.split('=', 1)
            cfg[k] = v


def api(metodo, ruta, cuerpo=None):
    req = urllib.request.Request(
        cfg['COOLIFY_URL'] + ruta,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        method=metodo,
        headers={'Authorization': 'Bearer ' + cfg['COOLIFY_TOKEN'],
                 'Content-Type': 'application/json',
                 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        t = e.read().decode()
        try:
            return e.code, json.loads(t)
        except Exception:
            return e.code, {'raw': t[:200]}


# Leer el archivo con las variables nuevas
nuevas = {}
with open(archivo) as f:
    for linea in f:
        linea = linea.strip()
        if not linea or linea.startswith('#') or '=' not in linea:
            continue
        k, v = linea.split('=', 1)
        nuevas[k.strip()] = v.strip().strip('"').strip("'")

if not nuevas:
    print('  el archivo no tiene variables')
    raise SystemExit(1)

codigo, actuales = api('GET', f'/api/v1/applications/{app_uuid}/envs')
existentes = {e['key']: e for e in actuales
              if isinstance(actuales, list) and not e.get('is_preview')}

creadas = actualizadas = fallidas = 0
for k, v in nuevas.items():
    if k in existentes:
        c, r = api('PATCH', f'/api/v1/applications/{app_uuid}/envs',
                   {'key': k, 'value': v, 'is_preview': False})
        estado = 'actualizada'
    else:
        c, r = api('POST', f'/api/v1/applications/{app_uuid}/envs',
                   {'key': k, 'value': v, 'is_preview': False})
        estado = 'creada'
    if c in (200, 201):
        print(f'  {k:34s} {estado}')
        if estado == 'creada':
            creadas += 1
        else:
            actualizadas += 1
    else:
        print(f'  {k:34s} FALLO ({c}) {str(r)[:90]}')
        fallidas += 1

print()
print(f'  {creadas} creadas, {actualizadas} actualizadas, {fallidas} con error')
print('  Recuerda redesplegar la app para que tomen efecto.')
