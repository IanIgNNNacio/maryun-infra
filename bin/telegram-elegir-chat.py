#!/usr/bin/env python3
"""Descubre a que conversaciones puede escribir el bot y deja elegir una.

Por que no basta con tomar la ultima: si Ian le escribe al bot en privado Y lo
agrega a un grupo, la ultima no es necesariamente la que quiere. Y el
identificador de un grupo es NEGATIVO, asi que confundirlos manda los avisos al
lugar equivocado sin error visible.

Mira tres tipos de novedad:
  message           mensajes (en grupo, solo los dirigidos al bot si tiene la
                    privacidad activada, que es lo normal)
  my_chat_member    cuando agregan o quitan al bot de un grupo. Este llega
                    SIEMPRE, sin importar la privacidad: es la forma fiable de
                    descubrir un grupo recien creado.
  channel_post      canales

El menu sale por la salida de error y el identificador elegido por la salida
estandar, para que quien lo llama capture solo el numero.
"""
import json
import os
import sys
import urllib.request

API = "https://api.telegram.org"
TOKEN = os.environ["TELEGRAM_TOKEN"]

TIPOS = {
    "private": "privada",
    "group": "grupo",
    "supergroup": "supergrupo",
    "channel": "canal",
}


def aviso(*a):
    print(*a, file=sys.stderr)


def novedades():
    url = f"{API}/bot{TOKEN}/getUpdates?timeout=0&limit=100"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r).get("result", [])


def nombre(chat):
    if chat.get("title"):
        return chat["title"]
    partes = [chat.get("first_name"), chat.get("last_name")]
    n = " ".join(p for p in partes if p)
    return n or chat.get("username") or "sin nombre"


chats = {}
for u in novedades():
    for clave in ("message", "edited_message", "channel_post", "my_chat_member"):
        if clave in u and "chat" in u[clave]:
            c = u[clave]["chat"]
            chats[c["id"]] = c

if not chats:
    aviso("  No se encontro ninguna conversacion.")
    aviso("")
    aviso("  Para una conversacion privada: escribele cualquier cosa al bot.")
    aviso("  Para un grupo: agrega el bot al grupo. Si aun asi no aparece,")
    aviso("  escribe en el grupo /start@<usuario_del_bot> — con la privacidad")
    aviso("  activada el bot solo ve los mensajes dirigidos a el.")
    sys.exit(1)

orden = sorted(chats.values(), key=lambda c: (c["type"] != "private", nombre(c)))

aviso("")
aviso("  Conversaciones donde el bot puede escribir:")
aviso("")
for i, c in enumerate(orden, 1):
    tipo = TIPOS.get(c["type"], c["type"])
    aviso(f"    {i}) {nombre(c):32} {tipo:11} id {c['id']}")
aviso("")

if len(orden) == 1:
    elegido = orden[0]
    aviso(f"  Solo hay una: se usa {nombre(elegido)}")
else:
    while True:
        aviso("  Numero de la que quieres usar: ")
        try:
            n = int(sys.stdin.readline().strip())
            elegido = orden[n - 1]
            break
        except (ValueError, IndexError):
            aviso("  Numero invalido.")

if elegido["type"] == "group":
    aviso("")
    aviso("  AVISO: es un grupo normal. Si Telegram lo convierte a supergrupo")
    aviso("  -pasa al hacerlo publico, agregar historial o superar los 200")
    aviso("  miembros- su identificador CAMBIA y los avisos dejan de llegar")
    aviso("  sin dar error. Si eso ocurre, vuelve a ejecutar el configurador.")

print(elegido["id"])
