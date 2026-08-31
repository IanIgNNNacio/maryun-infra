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
#       sudo /srv/bin/configurar-telegram.sh --cambiar-chat
#
# --cambiar-chat conserva el token y solo vuelve a elegir el destino. Sirve
# para pasar de una conversacion privada a un grupo, o cuando Telegram
# convierte un grupo en supergrupo y le cambia el identificador.

set -uo pipefail

SECRETOS=/srv/secrets/monitoreo.env
API=https://api.telegram.org
CAMBIAR_CHAT=0

while [ $# -gt 0 ]; do
    case "$1" in
        --cambiar-chat) CAMBIAR_CHAT=1; shift;;
        *) echo "opcion desconocida: $1"; exit 2;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "Ejecutalo con sudo."; exit 1; }
[ -r "$SECRETOS" ] || { echo "Falta $SECRETOS"; exit 1; }

# ─────────────────────────────────────────────────────────────── 1. token ──
if grep -q '^TELEGRAM_TOKEN=' "$SECRETOS" 2>/dev/null; then
    echo "Ya hay un token guardado: se reutiliza."
    . "$SECRETOS"
    if [ "$CAMBIAR_CHAT" -eq 1 ]; then
        echo "Se elegira un destino nuevo."
        TELEGRAM_CHAT=""
    fi
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
#
# Puede ser una conversacion privada o un GRUPO. Un grupo es mejor para un
# equipo: los avisos no dependen de que una sola persona los vea.
#
# Para una privada: escribele cualquier cosa al bot.
# Para un grupo:    agrega el bot al grupo. Si no aparece, escribe ahi
#                   /start@<usuario_del_bot> — con la privacidad activada
#                   (lo normal) el bot solo ve los mensajes dirigidos a el.
#
# No se toma "la ultima conversacion": si existen las dos, elegir mal manda los
# avisos al lugar equivocado sin dar ningun error. Se muestran y se elige.
if [ -z "${TELEGRAM_CHAT:-}" ]; then
    echo
    echo "=== buscando conversaciones ==="
    echo "  Si aun no lo hiciste, hazlo AHORA desde Telegram:"
    echo "    privado -> busca @$BOT, pulsa Iniciar y mandale un mensaje"
    echo "    grupo   -> agrega @$BOT al grupo"
    echo "  Un bot no puede escribir primero: necesita que tu abras la puerta."

    for intento in 1 2 3 4 5 6; do
        TELEGRAM_CHAT="$(TELEGRAM_TOKEN="$TELEGRAM_TOKEN" \
                         python3 /srv/bin/telegram-elegir-chat.py)" && break
        echo "  esperando... ($intento/6)"
        sleep 10
    done
    if [ -z "${TELEGRAM_CHAT:-}" ]; then
        echo "  No aparecio ninguna conversacion. Vuelve a ejecutar esto."
        exit 1
    fi
fi
echo "  conversacion elegida: $TELEGRAM_CHAT"

# ──────────────────────────────────────────────────────────── 3. guardar ──
if grep -q '^TELEGRAM_TOKEN=' "$SECRETOS" 2>/dev/null; then
    # Ya existia: solo se actualiza el destino, que es lo unico que pudo cambiar.
    if grep -q '^TELEGRAM_CHAT=' "$SECRETOS"; then
        sed -i "s|^TELEGRAM_CHAT=.*|TELEGRAM_CHAT=$TELEGRAM_CHAT|" "$SECRETOS"
    else
        echo "TELEGRAM_CHAT=$TELEGRAM_CHAT" >> "$SECRETOS"
    fi
    echo "  destino actualizado en $SECRETOS"
else
    {
      echo
      echo "# Canal puente de avisos: Telegram. No depende de dominio verificado."
      echo "TELEGRAM_TOKEN=$TELEGRAM_TOKEN"
      echo "TELEGRAM_CHAT=$TELEGRAM_CHAT"
    } >> "$SECRETOS"
    echo "  guardado en $SECRETOS"
fi
chmod 0640 "$SECRETOS"
chown root:maryun "$SECRETOS"

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
