# Monitoreo de maryun01

Dos herramientas que responden preguntas distintas. Ninguna se publica a
internet: ambas escuchan solo en la VPN.

| | Uptime Kuma | Beszel |
|---|---|---|
| Pregunta | ¿responde el servicio? | ¿aguanta el servidor? |
| Dirección | http://10.8.0.1:3001 | http://10.8.0.1:8090 |
| Credenciales | `/srv/secrets/monitoreo.env` | `/srv/secrets/monitoreo.env` |

Stack en `/srv/stacks/monitoreo/`. Datos en `./datos/` (dentro de `/srv`, o sea
respaldados).

---

## Qué vigila Uptime Kuma

13 monitores, cada 60 s.

| Monitor | Cómo |
|---|---|
| ERP producción / preview | `https://coolify-proxy/` con cabecera `Host` |
| Metabase, Superset, Mage, ClickHouse, Coolify, Beszel | endpoint HTTP de salud |
| PostgreSQL ERP / Mage, Túnel MySis, ClickHouse nativo | puerto TCP |
| Respaldo diario | *push*: el respaldo avisa; si calla 26 h, salta |

### Por qué el ERP se vigila en `/acceso` y sin seguir redirecciones

`erp.maryun.cl` todavía resuelve a **Vercel** y `preview.maryun.cl` al **VPS
viejo**. Un monitor contra esas URLs vigilaría los servidores antiguos y no
avisaría de una caída de maryun01.

Por eso los monitores van contra el proxy de este servidor con la cabecera
`Host`. **Pero eso solo no basta, y costó descubrirlo:**

> El ERP responde **307 hacia `/acceso`**, una ruta relativa. Al seguirla, el
> cliente HTTP de Uptime Kuma reconstruye la URL usando la **cabecera `Host`**
> en vez del destino de la conexión — y ese segundo salto **sale a internet**.
>
> Resultado: durante las primeras horas, el monitor de producción estuvo
> midiendo **Vercel**, y el de preview el **VPS viejo**. Exactamente lo que la
> cabecera `Host` pretendía evitar.

Cómo se comprobó, espiando el espacio de red del contenedor:

```bash
nsenter -t $(docker inspect -f '{{.State.Pid}}' uptime-kuma) -n \
        ss -tn state established
```

En 75 segundos aparecieron `216.150.1.65` (Vercel) y `51.222.28.249` (VPS
viejo). Los tiempos cuadraban: Kuma reportaba 197-217 ms para producción, y un
`curl` a `erp.maryun.cl/acceso` desde el servidor daba 200 ms.

> Con `ss ... state established`, `ss` **omite la columna de estado**: las
> direcciones quedan en `$3` y `$4`, no en `$4` y `$5`.

**La configuración correcta**, que nunca sale del servidor:

| | |
|---|---|
| URL | `https://coolify-proxy/acceso` |
| Cabecera | `Host: erp.maryun.cl` / `preview.maryun.cl` |
| `maxredirects` | **0** |
| Códigos aceptados | **200-299** solamente |

Se pide la página de login completa a propósito, no un endpoint trivial: si el
ERP fallara al renderizarla, un chequeo superficial no lo vería.

**Lección general: poner la cabecera `Host` no basta si la respuesta redirige.**
Hay que quitar la redirección del chequeo, no solo el DNS de la primera
petición.

> **Al trasladar los dominios:** cambiar ambos monitores a la URL pública. Ahí
> sí conviene seguir redirecciones —se quiere comprobar la cadena completa— y
> además Uptime Kuma empieza a avisar del vencimiento del certificado.

### Por qué no se vigila por nombre de contenedor

Coolify le agrega a cada aplicación un sufijo de despliegue
(`n5hirwwi5dytybx4pizafony-045707833382`) que **cambia en cada deploy**. Un
monitor atado a ese nombre se rompe solo al siguiente despliegue.

---

## Qué vigila Beszel

Métricas del servidor cada 60 s, con histórico: CPU, RAM, disco (`/`, `/srv`,
`/var/lib/docker`), red, temperatura y uso por contenedor.

### Reglas de alerta

| Regla | Umbral | Sostenido | Por qué |
|---|---|---|---|
| Status | caído | 1 min | el servidor deja de reportar |
| CPU | 80 % | 10 min | una punta de un minuto es normal; diez, no |
| Memory | 85 % | 5 min | antes de que el kernel empiece a matar procesos |
| Disk | 85 % | 1 min | un disco lleno tumba Postgres y ClickHouse |
| LoadAvg5 | 8 | 5 min | mitad de los 16 núcleos en cola |

### El agente

Corre **nativo** (`systemd`, paquete `beszel-agent`), no en contenedor: un
agente dentro de un contenedor mide el contenedor, no la máquina, y reportaría
disco y memoria equivocados.

Se conecta **saliente** al hub por websocket. No abre ningún puerto.

---

## Ningún contenedor recibe el socket de Docker

Montar `/var/run/docker.sock`, aunque sea de solo lectura, **entrega root del
servidor**: quien lo alcanza puede crear un contenedor privilegiado que monte
`/`. Es una de las formas más comunes de convertir un fallo menor en un
compromiso total — y este servidor ya vivió uno.

En su lugar hay un proxy (`tecnativa/docker-socket-proxy`) que solo deja pasar
consultas de lectura sobre contenedores. Verificado:

```
GET  /containers/json     → 200, ve los 26 contenedores
POST /containers/create   → 403
POST /containers/*/exec   → 403
escucha en 127.0.0.1:2375, no accesible desde fuera
```

El paquete `.deb` del agente agrega el usuario `beszel` al grupo `docker`
—equivalente a root—. **Se le quitó**; usa el proxy.

---

## Respaldo diario

`maryun-respaldo.timer` dispara a las 03:15 (± 20 min) `respaldo-diario.sh`, que
encadena las tres capas y avisa a Uptime Kuma.

```bash
systemctl list-timers maryun-respaldo.timer
journalctl -u maryun-respaldo.service -n 50
```

---

## Canal de avisos

| Canal | Estado |
|---|---|
| Correo (SMTP de Resend) | configurado, **bloqueado**: `maryun.cl` no está verificado en Resend |
| Telegram | canal puente, no depende de dominio verificado |

```bash
sudo /srv/bin/configurar-telegram.sh                  # primera vez
sudo /srv/bin/configurar-telegram.sh --cambiar-chat   # cambiar de destino
```

Pide el token por entrada oculta (no queda en el historial), lo valida contra
`getMe`, deja **elegir** la conversación, envía un mensaje de prueba y solo
entonces configura Kuma y Beszel. Si la prueba falla, se detiene ahí en vez de
dejar creer que quedó configurado.

### Grupo en vez de conversación privada

Preferible para un equipo: los avisos no dependen de que una sola persona los
vea. Basta con **agregar el bot al grupo**; el script lo detecta por el evento
`my_chat_member`, que llega aunque el bot tenga la privacidad activada.

Tres cosas que cambian respecto a una conversación privada:

- **El identificador de un grupo es negativo** (`-100…`). Por eso el script no
  toma "la última conversación": elegir mal manda los avisos al lugar
  equivocado **sin dar ningún error**.
- **Un bot en un grupo no ve los mensajes normales** por omisión, solo los que
  empiezan con `/` o lo mencionan. Para enviar da igual; importa solo para
  descubrirlo. Si no aparece, escribe `/start@<usuario_del_bot>` en el grupo.
- **Si el grupo pasa a supergrupo, su identificador cambia** y los avisos dejan
  de llegar en silencio. Ocurre al hacerlo público, agregar historial o superar
  los 200 miembros. Solución: `--cambiar-chat`.

### Trampa al configurar los destinos de Beszel

Al **crear** el registro de `user_settings` (POST), Beszel sobrescribe lo
enviado con sus valores por omisión y **pierde los `webhooks`**. Al
**actualizarlo** (PATCH) sí los respeta. Con el mismo cuerpo exacto:

```
POST  → {"chartTime":"1h","emails":[...]}                 ← sin webhooks
PATCH → {"chartTime":"1h","emails":[...],"webhooks":[1]}   ← correcto
```

Por eso `beszel-telegram.py` crea y **después** actualiza, siempre en dos
pasos, y **falla ruidosamente si al final quedan cero**. Anunciar éxito sin
comprobarlo deja el monitoreo mudo justo cuando hace falta.

URL de shoutrrr para Telegram: `telegram://<token>@telegram?chats=<chat_id>`
— el `chat_id` de un grupo es **negativo**.

### Lo que ve quien esté en el grupo

Nombre del servicio y su estado (`ERP produccion — caído`), más las métricas de
saturación. Sin credenciales ni datos de negocio. Aun así, quien esté en el
grupo sabe cuándo la infraestructura está débil: conviene que sea un grupo
cerrado.

---

## Lo que este monitoreo NO cubre

**Si el servidor entero se cae, el monitoreo se cae con él y nadie avisa.**

Es una limitación real, no un detalle: Uptime Kuma y Beszel corren *dentro* de
maryun01. Detectan que un contenedor murió, que el disco se llena o que el ERP
devuelve error — pero no que el servidor dejó de existir.

Para cubrirlo hace falta algo **fuera** del servidor que espere una señal
periódica y avise cuando deje de llegar. Pendiente de decidir el canal.
