#!/usr/bin/env python3
"""Agrega Telegram como destino de avisos en Beszel.

Beszel entrega por shoutrrr; su URL de Telegram es

    telegram://<token>@telegram?chats=<chat_id>

Se guarda tambien el correo, que empezara a funcionar solo en cuanto el
dominio maryun.cl este verificado en Resend.

OJO con una trampa de Beszel: al CREAR el registro de user_settings sobrescribe
lo enviado con sus valores por omision, y los webhooks se pierden. Al
ACTUALIZARLO los respeta. Por eso, cuando no existe, se crea primero y se
actualiza despues en dos pasos.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

HUB = "http://10.8.0.1:8090"
SECRETOS = "/srv/secrets/monitoreo.env"

cfg = {}
for linea in open(SECRETOS):
    linea = linea.strip()
    if linea and not linea.startswith("#") and "=" in linea:
        clave, valor = linea.split("=", 1)
        cfg[clave] = valor.strip().strip('"')


def pedir(ruta, datos=None, metodo=None, token=None):
    # Un GET con cuerpo confunde al servidor: el metodo se deduce del cuerpo.
    if metodo is None:
        metodo = "POST" if datos is not None else "GET"
    req = urllib.request.Request(HUB + ruta, method=metodo)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", token)
    cuerpo = json.dumps(datos).encode() if datos is not None else None
    try:
        with urllib.request.urlopen(req, cuerpo, timeout=40) as r:
            texto = r.read().decode()
            try:
                return r.status, json.loads(texto)
            except json.JSONDecodeError:
                return r.status, texto[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


_, auth = pedir(
    "/api/collections/_superusers/auth-with-password",
    {"identity": cfg["BESZEL_USUARIO"], "password": cfg["BESZEL_PASS"]},
)
tok = auth["token"]

_, usuarios = pedir("/api/collections/users/records?perPage=1", token=tok)
uid = usuarios["items"][0]["id"]
correo = usuarios["items"][0]["email"]

webhook = "telegram://{}@telegram?chats={}".format(
    cfg["TELEGRAM_TOKEN"], cfg["TELEGRAM_CHAT"]
)
ajustes = {"webhooks": [webhook], "emails": [correo], "chartTime": "1h"}

filtro = ("/api/collections/user_settings/records?perPage=1&filter="
          + urllib.parse.quote("user='{}'".format(uid)))
_, existentes = pedir(filtro, token=tok)
items = existentes.get("items", []) if isinstance(existentes, dict) else []

if not items:
    codigo, resp = pedir("/api/collections/user_settings/records",
                         {"user": uid, "settings": ajustes}, token=tok)
    if codigo not in (200, 201):
        print("  fallo al crear: HTTP {} {}".format(codigo, resp))
        raise SystemExit(1)
    rid = resp["id"]
else:
    rid = items[0]["id"]

# Segundo paso siempre: es el unico que Beszel respeta.
codigo, resp = pedir("/api/collections/user_settings/records/" + rid,
                     {"settings": ajustes}, metodo="PATCH", token=tok)
if codigo != 200:
    print("  fallo al actualizar: HTTP {} {}".format(codigo, resp))
    raise SystemExit(1)

s = resp.get("settings", {})
n_webhooks = len(s.get("webhooks", []) or [])
correos = s.get("emails", []) or []

print("  destinos por webhook: {}".format(n_webhooks))
print("  destinos por correo:  {}".format(", ".join(correos) or "ninguno"))

# Se comprueba de verdad en vez de anunciar exito: un canal que no quedo
# guardado deja el monitoreo mudo justo cuando hace falta.
if n_webhooks == 0:
    print()
    print("  ERROR: el webhook de Telegram NO quedo guardado.")
    print("  Beszel no avisara por Telegram. Revisar antes de dar esto por hecho.")
    raise SystemExit(1)

print("  Telegram guardado correctamente")
print("  (el correo empezara a funcionar al verificar el dominio en Resend)")
