# Vigilancia desde fuera del servidor

## El agujero que tapa

Había tres monitores y los tres corrían **dentro** de maryun01:

| | Qué vigila | Dónde corre |
|---|---|---|
| Uptime Kuma | servicios por HTTP | en maryun01 |
| Beszel | CPU, memoria, disco, SMART | en maryun01 |
| Sentinel de Coolify | contenedores | en maryun01 |

Los tres son útiles para «un servicio se cayó». Ninguno sirve para «la máquina
se cayó», porque el que tenía que avisar se cae con ella.

No es hipotético. El 1 y el 2 de septiembre de 2026 hubo dos reinicios en frío
de unos dos minutos cada uno y **nadie recibió ningún aviso**. Se descubrieron
después, leyendo `journalctl --list-boots`. Están documentados en
[ticket-ovh-reinicios.md](ticket-ovh-reinicios.md).

## Qué se instaló

`.github/workflows/vigilancia-externa.yml`, en este mismo repositorio. Corre en
la infraestructura de GitHub, así que es independiente del servidor por
construcción, no por configuración.

Cada 30 minutos comprueba:

| Servicio | URL | Se acepta |
|---|---|---|
| ERP producción | `https://erp.maryun.cl/` | 200, 307, 308 |
| ERP preview | `https://preview.maryun.cl/` | 200, 307, 308 |
| Metabase | `https://metabase.maryun.cl/api/health` | 200 |
| Superset | `https://superset.maryun.cl/health` | 200 |

Se aceptan varios códigos a propósito: el ERP responde 307 porque redirige a la
pantalla de acceso, y eso es una respuesta sana. Exigir 200 daría una alarma
falsa permanente, y una alarma que siempre suena es una alarma que nadie mira.

Y comprueba que a los certificados les queden **más de 14 días**. Los renueva
Traefik por DNS-01 contra Cloudflare; si esa renovación se rompe, no se nota
hasta que caducan y entonces cae todo junto. Hoy quedan 63 días.

Cada comprobación se reintenta una vez antes de dar por caído algo, porque un
fallo aislado de red entre GitHub y Cloudflare no es una caída del servidor.

**Cómo avisa:** si algo falla, el trabajo termina en error y GitHub manda el
correo de «workflow run failed». No hace falta cuenta ni servicio nuevo.

## Tres trampas que conviene conocer

**1. GitHub desactiva los cron de un repositorio sin actividad.** Si nadie
comitea en `maryun-infra` durante 60 días, GitHub deja de ejecutar el programado
y **avisa por correo una vez**. El monitor se apagaría en silencio si ese correo
se pierde. Cualquier commit reinicia el contador. No lo automaticé a propósito:
un commit cada 30 minutos para mantenerlo vivo llenaría el historial de ruido.

**2. Si se activa Cloudflare Access, esto empieza a fallar.** Access responde 302
hacia `cloudflareaccess.com` a quien no tenga sesión, y el monitor espera 200.
Al activarlo hay que añadir `302` a los códigos aceptados de Metabase y Superset,
o darle un token de servicio. Está anotado en
[acceso-bi-cloudflare.md](acceso-bi-cloudflare.md).

**3. El intervalo está elegido por coste.** GitHub cobra un mínimo de un minuto
por ejecución. Cada 30 minutos son ~1.440 ejecuciones y ~1.440 minutos al mes,
dentro de los 2.000 del plan gratuito para repositorios privados. **Cada 15
minutos se saldría del plan.**

## Si hace falta granularidad de un minuto

La vía sin coste es un **Cron Trigger de Cloudflare Workers**: el plan gratuito
admite ejecuciones cada minuto y 100.000 peticiones al día, y no consume minutos
de GitHub. Requiere acceso a la cuenta de Cloudflare, que el token del servidor
no tiene —está limitado a la zona de DNS, que es como debe estar—.

El Worker sería unas veinte líneas: recorrer la misma lista, y en caso de fallo
llamar a la API de Telegram. Que quede pendiente, no montado a medias.

## Para que avise por Telegram

Ya hay un bot: `/srv/bin/configurar-telegram.sh` y `/srv/bin/kuma-telegram.py`
lo usan, y su credencial vive en `/srv/secrets/monitoreo.env`.

Para que el workflow también avise, hay que añadir dos secretos al repositorio
en GitHub (Settings → Secrets and variables → Actions):

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

y un paso final con `if: failure()` que haga un POST a
`https://api.telegram.org/bot$TELEGRAM_TOKEN/sendMessage`.

No lo dejé puesto porque los secretos hay que crearlos desde la cuenta de GitHub
y un paso que referencia secretos inexistentes falla en silencio: el `curl` sale
con error y el aviso no llega, que es exactamente el fallo que este monitor
existe para no tener.

## Lo que sigue sin vigilar

- **Que los respaldos se estén subiendo.** Hoy `respaldo-diario.sh` escribe en
  `/var/log/maryun-respaldo.log` y avisa por Telegram, pero desde dentro. Un
  interruptor de hombre muerto —el servidor hace ping a un servicio externo al
  terminar el respaldo, y ese servicio avisa si el ping no llega— es la forma
  correcta, y necesita una cuenta externa.
- **La cola de despliegues de Coolify.** Si un despliegue se queda colgado, esto
  no lo ve: el sitio sigue respondiendo con la versión vieja.
