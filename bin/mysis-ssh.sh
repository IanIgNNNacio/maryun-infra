#!/usr/bin/env bash
#
# Consola en la VM de MySis (Azure, vmmysis, 20.153.168.52), para el trabajo de
# migracion a este servidor.
#
#   sudo /srv/bin/mysis-ssh.sh 'df -h'
#   sudo /srv/bin/mysis-ssh.sh < guion.sh          # le pasa un guion por stdin
#   sudo /srv/bin/mysis-ssh.sh --copiar origen destino
#
# Usa la MISMA llave que el tunel de MariaDB (/srv/secrets/mysis-ssh), asi que
# no hay una credencial mas que rotar. La llave no sale de este servidor.
#
# MYSIS ES PRODUCCION Y AQUI SOLO SE LEE. La regla de SERVIDOR.md no se relaja
# por la migracion: cualquier cosa que escriba en esa maquina se decide y se
# anota antes, no se improvisa desde una sesion.
set -euo pipefail

LLAVE=/srv/secrets/mysis-ssh/vmmysis_key.pem
CONOCIDOS=/srv/secrets/mysis-ssh/known_hosts
DESTINO=mrootuser@20.153.168.52

OPC=(-i "$LLAVE" -o "UserKnownHostsFile=$CONOCIDOS" -o StrictHostKeyChecking=yes
     -o ConnectTimeout=20 -o ServerAliveInterval=30 -o BatchMode=yes)

if [ "${1:-}" = "--copiar" ]; then
  exec scp "${OPC[@]}" "$2" "$DESTINO:$3"
fi

if [ $# -eq 0 ]; then
  # Sin argumentos: lo que venga por la entrada estandar es un guion de bash.
  exec ssh "${OPC[@]}" "$DESTINO" "bash -s"
fi

exec ssh "${OPC[@]}" "$DESTINO" "$@"
