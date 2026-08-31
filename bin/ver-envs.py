#!/usr/bin/env python3
"""Lista las variables de una app de Coolify mostrando el flag is_preview,
para distinguir duplicados reales de pares normal/preview."""
import json
import sys
import urllib.request

APP = sys.argv[1] if len(sys.argv) > 1 else 'n5hirwwi5dytybx4pizafony'

cfg = {}
with open('/srv/secrets/coolify-api.env') as f:
    for l in f:
        l = l.strip()
        if l and not l.startswith('#') and '=' in l:
            k, v = l.split('=', 1)
            cfg[k] = v

req = urllib.request.Request(
    f"{cfg['COOLIFY_URL']}/api/v1/applications/{APP}/envs",
    headers={'Authorization': 'Bearer ' + cfg['COOLIFY_TOKEN']})
envs = json.load(urllib.request.urlopen(req, timeout=30))

print(f'  total entradas: {len(envs)}')
print()
for e in sorted(envs, key=lambda x: (str(x.get('key')), str(x.get('is_preview')))):
    clave = str(e.get('key'))
    prev = str(e.get('is_preview'))
    print(f"    {clave:22s} is_preview={prev:6s} id={e.get('id')} uuid={e.get('uuid')}")
