#!/usr/bin/env bash
#
# Muestra a donde se conecta de verdad un contenedor, separando lo que se queda
# dentro del servidor de lo que sale a internet.
#
# Nacio de un caso real: los monitores del ERP en Uptime Kuma parecian medir
# maryun01, pero al seguir una redireccion relativa el cliente reconstruia la
# URL con la cabecera Host y salia a internet, midiendo Vercel. Ninguna
# configuracion revelaba eso; solo mirar las conexiones reales.
#
# Sirve para cualquier "esto deberia ser local, por que tarda tanto".
#
# Uso:  sudo /srv/bin/espiar-conexiones.sh <contenedor> [segundos]

set -uo pipefail

CONTENEDOR="${1:?falta el nombre del contenedor}"
SEGUNDOS="${2:-75}"

[ "$(id -u)" -eq 0 ] || { echo "Ejecutalo con sudo."; exit 1; }

PID="$(docker inspect -f '{{.State.Pid}}' "$CONTENEDOR" 2>/dev/null)"
[ -n "$PID" ] || { echo "No existe el contenedor $CONTENEDOR"; exit 1; }

echo "  espiando $CONTENEDOR durante ${SEGUNDOS}s"
echo

TMP="$(mktemp)"
fin=$(( $(date +%s) + SEGUNDOS ))
while [ "$(date +%s)" -lt "$fin" ]; do
    # OJO: con 'state established', ss OMITE la columna de estado, asi que la
    # direccion remota queda en $4 y no en $5. Ese detalle me costo una vuelta.
    nsenter -t "$PID" -n ss -tn state established 2>/dev/null \
      | awk 'NR>1 {print $4}' >> "$TMP"
    sleep 0.4
done

echo "=== destinos ==="
# ss envuelve las IPv4 como [::ffff:1.2.3.4]: se desenvuelven antes de nada.
sed -E 's/\[::ffff:([0-9.]+)\]/\1/' "$TMP" \
  | sed -E 's/^\[([^]]+)\]:[0-9]+$/\1/; s/:[0-9]+$//' \
  | grep -v '^$' | sort | uniq -c | sort -rn \
  | while read -r n ip; do
        case "$ip" in
            10.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|192.168.*|127.*) tipo="interna" ;;
            fd*|fe80*|::1)                                              tipo="interna (IPv6 privada)" ;;
            *)                                                          tipo="PUBLICA - sale a internet" ;;
        esac
        nombre="$(getent hosts "$ip" 2>/dev/null | awk '{print $2}')"
        printf "  %5s  %-40s %-28s %s\n" "$n" "$ip" "$tipo" "${nombre:-}"
    done

rm -f "$TMP"
