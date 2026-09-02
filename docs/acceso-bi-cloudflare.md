# Poner Cloudflare Access delante de Metabase y Superset

**Estado: preparado, sin activar.** Los pasos 1 y 2 se hacen en cuentas
(Cloudflare y Azure) a las que sólo llega Ian; el token de DNS del servidor está
limitado a la zona y no ve la cuenta, que es lo correcto pero impide
automatizarlo desde aquí.

## Por qué

Desde el 2 de septiembre de 2026, `metabase.maryun.cl` y `superset.maryun.cl`
resuelven a la IP pública. Antes resolvían a `10.8.0.1` y sólo se alcanzaban por
la VPN. La pantalla de acceso de las dos herramientas la ve ahora cualquiera en
internet.

Ese mismo día se endureció lo de dentro —contraseñas, roles, SQL libre, enlaces
públicos; está más abajo—, pero eso protege las cuentas, no evita que la
pantalla esté a la vista. Access es la puerta que faltaba.

## Lo que Access hace y lo que no

**No reemplaza el login de la herramienta.** Es una puerta delante: Cloudflare
autentica en el borde y después Metabase o Superset piden lo suyo. Serán **dos
logins**. Con Entra ID como proveedor, el primero es la cuenta de maryun que la
gente ya usa a diario, no un código al correo.

Superset **sí** podría tener un único login con Microsoft, porque su OAuth es
gratis. Metabase **no**: el que corre es `metabase/metabase:v0.63.16`, la
edición abierta, y ahí SAML y JWT son de pago.

## Paso 1 · Azure: registrar la aplicación

Entra ID → App registrations → New registration.

- Nombre: `Cloudflare Access — BI Maryun`
- Redirect URI, tipo **Web**:
  `https://<equipo>.cloudflareaccess.com/cdn-cgi/access/callback`
  El `<equipo>` es el nombre que se elige al activar Zero Trust en el paso 2, así
  que conviene decidirlo antes o volver a editar esto después.

Apuntar tres valores: **Application (client) ID**, **Directory (tenant) ID** y un
**client secret** nuevo (Certificates & secrets → New client secret).

Permisos de API → Microsoft Graph → delegados: `openid`, `profile`, `email`,
`offline_access`, `User.Read`. Con eso basta para autenticar por correo. Si más
adelante se quiere permitir por **grupo** de Entra en vez de por dominio del
correo, hacen falta además `Directory.Read.All` y `GroupMember.Read.All`, y ésos
piden consentimiento de administrador.

## Paso 2 · Cloudflare: Zero Trust y el proveedor

1. `dash.cloudflare.com` → Zero Trust. Al entrar la primera vez pide elegir el
   nombre del equipo y un plan: el **Free** cubre 50 usuarios, que sobra.
2. Settings → Authentication → Login methods → Add new → **Azure AD**. Se pegan
   el client ID, el client secret y el directory ID del paso 1.
3. Probar con «Test» ahí mismo antes de seguir. Si falla, casi siempre es que el
   redirect URI del paso 1 no coincide letra por letra con el nombre del equipo.

## Paso 3 · Las dos aplicaciones

Access → Applications → Add an application → **Self-hosted**, una por
herramienta:

| | Metabase | Superset |
|---|---|---|
| Nombre | Metabase Maryun | Superset Maryun |
| Dominio | `metabase.maryun.cl` | `superset.maryun.cl` |
| Duración de sesión | 24 h | 24 h |

`reportes.maryun.cl` apunta al mismo Metabase, así que hay que añadirlo también
—como segundo dominio de la misma aplicación— o queda una puerta trasera sin
puerta.

Política, en las dos: acción **Allow**, incluir → **Emails ending in** →
`@maryun.cl`.

Ojo con esto: hay tres cuentas con correo personal —dos `@gmail.com` en Superset
y una en Metabase—. Con la regla del dominio se quedan fuera. Si alguna se sigue
usando, hay que añadirla por «Emails» una a una, o pasarla a un correo de maryun,
que es lo sano.

## Paso 4 · Lo que se va a romper, y cómo evitarlo

Access intercepta **todas** las peticiones al nombre, no sólo las del navegador.
Dos cosas concretas de esta instalación se caen si no se prevé:

**El MCP de Metabase.** En `.claude.json` hay dos entradas: una a
`http://127.0.0.1:3000/api/mcp` y otra a `https://metabase.maryun.cl/api/mcp`. La
segunda dejará de funcionar. Lo más simple es usar sólo la de `127.0.0.1` por la
VPN. Si se necesita la pública, Access permite un **service token** y una política
de tipo Bypass para esa ruta.

*Nota aparte: hoy, sin Access, esa URL pública ya devuelve 403 «error 1010» a
clientes que no parecen un navegador, porque el proxy de Cloudflare aplica su
comprobación de integridad. O sea que en la práctica ya sólo funciona la de la
VPN.*

**El embed de Metabase.** `enable-embedding-static` está en `true`. Si algún sitio
incrusta un tablero, el iframe dejará de cargar. Hay que decidir: o se desactiva
el embed —si ya nadie lo usa—, o se le da un bypass. En Superset el embed está
activo por bandera pero `EMBED_ALLOWED_ORIGINS` está vacío, así que hoy nadie
puede incrustar y no hay nada que romper.

## Paso 5 · Comprobar

Desde una ventana privada, sin la VPN:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://metabase.maryun.cl/
```

Con Access puesto debe responder **302** hacia `cloudflareaccess.com`, no 200. Un
200 significa que la aplicación no está cubriendo ese nombre.

## Lo que ya está hecho por dentro (2 de septiembre de 2026)

Metabase:

- La contraseña mínima pasó de 6 caracteres y 1 dígito a
  `{total 8, minúsculas 2, mayúsculas 2, dígito 1, especial 1}`. Se fija con
  `MB_PASSWORD_COMPLEXITY=strong` en el compose: por la API devuelve 500, porque
  el ajuste sólo se lee de la variable de entorno.
- «Compartir públicamente» quedó en `false` explícito. **Estaba activo**, y el
  tablero «Cobranzas» tenía un enlace público que respondía HTTP 200 sin sesión
  desde internet. El enlace se revocó y el identificador quedó en blanco.
- El grupo `All Users` tenía `create-queries = query-builder-and-native` sobre
  ClickHouse: SQL libre para cualquiera, que se salta los permisos de colección.
  Pasó a `query-builder`.
- El mismo grupo podía **modificar** las 21 colecciones, la raíz y la papelera.
  Pasó a solo lectura en las 19 que lo tenían.
- La clave de API de administrador se rotó, porque se filtró en un mensaje de
  error durante estos mismos cambios.

Superset:

- `vbecerra@gmail.com` tenía rol Admin con último acceso en diciembre de 2025.
  Pasó a `only_read`.
- El rol `only_read` tenía `can_write` sobre Dashboard. El nombre engañaba; ya no
  tiene ningún permiso de escritura.
- `isabela.delamaza@maryun.cl` tenía `Gamma` además de `only_read`, y Gamma
  permite crear gráficos y conjuntos de datos. Se le quitó Gamma.
- `FAB_PASSWORD_COMPLEXITY_ENABLED = True`. Antes no se validaba nada.

Administradores, en las dos: **Ian y Felipe**, nadie más.

En ninguna de las dos herramientas el cambio de política invalida las
contraseñas que ya existen: sólo se aplica a las nuevas y a los cambios. Hay que
pedir el cambio a quien tenga una débil.

## Lo que queda pendiente de decidir

- `enable-embedding-static` en Metabase sigue en `true`. Si nadie incrusta,
  apagarlo quita superficie.
- `download-results = one-million-rows` para `All Users`: cualquiera puede
  exportar un millón de filas. Es normal en BI, pero con la herramienta pública
  quizá convenga bajarlo.
- Las tres cuentas con correo personal (`cianignacios@gmail.com`,
  `vbecerra@gmail.com`, `fmazav@gmail.com`). La de `cianignacios` se llama
  «Embebido Superset» y puede estar sirviendo un tablero incrustado, así que no se
  tocó.
