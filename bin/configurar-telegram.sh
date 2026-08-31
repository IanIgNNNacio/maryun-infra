#!/usr/bin/env bash
#
# Conecta el monitoreo de maryun01 a Telegram.
#
# Telegram va como canal PUENTE mientras el dominio maryun.cl no este verificado
# en Resend: no depende de dominio, es saliente y no expone nada del servidor.
# Cuando el correo funcione, los dos canales conviven sin tocar nada.
#
# El token se pide por entrada oculta para que no quede en el historial del
# shell. Un token de bot permite escribir como ese bot: es una credencial.
#
# Uso:  sudo /srv/bin/configurar-telegram.sh

set -uo pipefail

SECRETOS=/srv/secrets/monitoreo.env
API=https://api.telegram.org

[ "$(id -u)" -eq 0 ] || { echo "Ejecutalo con sudo."; exit 1; }
[ -r "$SECRETOS" ] || { echo "Falta $SECRETOS"; exit 1; }

# ─────────────────────────────────────────────────────────────── 1. token ──
if grep -q '^TELEGRAM_TOKEN=' "$SECRETOS" 2>/dev/null; then
    echo "Ya hay un token guardado: se reutiliza."
    . "$SECRETOS"
else
    echo "Pega el token que te dio BotFather (no se vera al escribir):"
    read -r -s TELEGRAM_TOKEN
    echo
    [ -n "$TELEGRAM_TOKEN" ] || { echo "Token vacio."; exit 1; }
fi

echo
echo "=== comprobando el token ==="
BOT="$(curl -s --max-time 25 "$API/bot$TELEGRAM_TOKEN/getMe" \
       | python3 -c 'import sys, json
d = json.load(sys.stdin)
print(d["result"]["username"] if d.get("ok") else "")' 2>/dev/null)"
if [ -z "$BOT" ]; then
    echo "  El token no sirve. Revisa que este completo (formato 123456789:AA...)."
    exit 1
fi
echo "  bot verificado: @$BOT"

# ────────────────────────────────────────────── 2. a que conversacion escribe ──
if [ -z "${TELEGRAM_CHAT:-}" ]; then
    echo
    echo "=== buscando tu conversacion ==="
    echo "  Si aun no le escribiste al bot, hazlo AHORA desde Telegram:"
    echo "  busca @$BOT, pulsa Iniciar y mandale cualquier mensaje."
    echo "  Un bot no puede escribir primero: necesita que tu abras la conversacion."
    echo
    for intento in 1 2 3 4 5 6; do
        TELEGRAM_CHAT="$(curl -s --max-time 25 "$API/bot$TELEGRAM_TOKEN/getUpdates" \
          | python3 -c 'import sys, json
d = json.load(sys.stdin)
ids = [u["message"]["chat"]["id"] for u in d.get("result", []) if "message" in u]
print(ids[-1] if ids else "")' 2>/dev/null)"
        [ -n "$TELEGRAM_CHAT" ] && break
        echo "  esperando tu mensaje... ($intento/6)"
        sleep 10
    done
    if [ -z "$TELEGRAM_CHAT" ]; then
        echo "  No llego ningun mensaje. Escribele al bot y vuelve a ejecutar esto."
        exit 1
    fi
fi
echo "  conversacion: $TELEGRAM_CHAT"

# ──────────────────────────────────────────────────────────── 3. guardar ──
if ! grep -q '^TELEGRAM_TOKEN=' "$SECRETOS" 2>/dev/null; then
    {
      echo
      echo "# Canal puente de avisos: Telegram. No depende de dominio verificado."
      echo "TELEGRAM_TOKEN=$TELEGRAM_TOKEN"
      echo "TELEGRAM_CHAT=$TELEGRAM_CHAT"
    } >> "$SECRETOS"
    chmod 0640 "$SECRETOS"
    chown root:maryun "$SECRETOS"
    echo "  guardado en $SECRETOS"
fi

# ───────────────────────────────────── 4. prueba real, no solo configuracion ──
echo
echo "=== enviando un mensaje de prueba ==="
MENSAJE="[maryun01] Canal de avisos conectado. Si lees esto, las alertas de Uptime Kuma y Beszel llegaran aqui."
OK="$(curl -s --max-time 25 -X POST "$API/bot$TELEGRAM_TOKEN/sendMessage" \
      -d "chat_id=$TELEGRAM_CHAT" \
      --data-urlencode "text=$MENSAJE" \
      | python3 -c 'import sys, json; print(json.load(sys.stdin).get("ok"))' 2>/dev/null)"
if [ "$OK" != "True" ]; then
    echo "  No se pudo enviar. Revisa el chat_id."
    exit 1
fi
echo "  ENVIADO — revisa Telegram antes de seguir"

# ──────────────────────────────────────────────────────────── 5. Uptime Kuma ──
echo
echo "=== conectando Uptime Kuma ==="
set -a
. "$SECRETOS"
set +a
docker run --rm --network coolify \
  -v /srv/bin/kuma-telegram.py:/t.py:ro \
  -e KUMA_USUARIO -e KUMA_PASS -e TELEGRAM_TOKEN -e TELEGRAM_CHAT \
  python:3.12-slim \
  sh -c 'pip install --quiet --no-cache-dir uptime-kuma-api >/dev/null 2>&1 && python /t.py'

# ────────────────────────────────────────────────────────────────── 6. Beszel ──
echo
echo "=== conectando Beszel ==="
python3 /srv/bin/beszel-telegram.py

echo
echo "=== listo ==="
echo "  Telegram queda como canal puente."
echo "  Al verificar maryun.cl en Resend, el correo se suma solo: ya esta configurado."
