#!/usr/bin/env python3
"""Cliente minimo de la API de Coolify.

Uso:
    coolify-api.py GET  /api/v1/servers
    coolify-api.py POST /api/v1/security/keys '{"name":"x","private_key":"..."}'
    coolify-api.py POST /api/v1/... @/ruta/al/cuerpo.json

Lee la credencial de /srv/secrets/coolify-api.env; nunca la imprime.
"""
import json
import sys
import urllib.error
import urllib.request

ENV = '/srv/secrets/coolify-api.env'

cfg = {}
with open(ENV) as f:
    for linea in f:
        linea = linea.strip()
        if linea and not linea.startswith('#') and '=' in linea:
            k, v = linea.split('=', 1)
            cfg[k] = v

metodo = sys.argv[1].upper()
ruta = sys.argv[2]
cuerpo = None

if len(sys.argv) > 3:
    crudo = sys.argv[3]
    if crudo.startswith('@'):
        with open(crudo[1:]) as f:
            crudo = f.read()
    cuerpo = crudo.encode()

req = urllib.request.Request(
    cfg['COOLIFY_URL'] + ruta,
    data=cuerpo,
    method=metodo,
    headers={
        'Authorization': 'Bearer ' + cfg['COOLIFY_TOKEN'],
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    },
)

try:
    with urllib.request.urlopen(req, timeout=60) as r:
        texto = r.read().decode()
        print(f'HTTP {r.status}')
except urllib.error.HTTPError as e:
    texto = e.read().decode()
    print(f'HTTP {e.code}')
except Exception as e:
    print(f'ERROR: {e}')
    raise SystemExit(1)

try:
    print(json.dumps(json.loads(texto), indent=2, ensure_ascii=False)[:3000])
except Exception:
    print(texto[:1500])
