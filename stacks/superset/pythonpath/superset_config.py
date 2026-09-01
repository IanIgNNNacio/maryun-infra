"""
Configuración central de Superset — Maryun.

Revisión de seguridad aplicada el 2026-08-14. Resumen de lo que cambió:

  1. SECRET_KEY y GUEST_TOKEN_JWT_SECRET ya NO tienen valor por defecto.
     Antes, si la variable de entorno faltaba, Superset arrancaba con
     "change-this-in-prod" y con un secreto corto — y nadie se enteraba.
     Ahora el arranque FALLA con un mensaje claro. Es preferible un
     contenedor que no levanta a uno que levanta inseguro.

  2. GUEST_ROLE_NAME pasó de "Admin" a "Embedded". Los tokens de invitado
     recibían permisos de administrador sobre toda la instancia.

  3. Cookies marcadas como Secure y SameSite coherente con HTTPS, ahora que
     Superset corre detrás de Traefik con TLS.

  4. Se quitó X-Frame-Options: ALLOWALL. El control de quién puede embeber
     queda solo en el CSP frame-ancestors, que es el mecanismo que respetan
     los navegadores actuales y que sí lee la lista de orígenes permitidos.

  5. EMBED_ALLOWED_ORIGINS quedó vacío por defecto. La lista anterior tenía
     18 orígenes, la mayoría de proyectos ajenos o instalaciones muertas.
     Si se vuelve a usar el embed, se declaran por variable de entorno.

  6. El MCP exige autenticación por defecto (MCP_AUTH_ENABLED). Antes estaba
     en False confiando en que el puerto no estuviera expuesto.

  7. Las URLs que apuntaban a IPs de otros despliegues ahora salen de
     variables de entorno, con el dominio real como valor por defecto.
"""

from __future__ import annotations

import os
from typing import Iterable

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def env(name: str, default: str | None = None) -> str | None:
    """Lee una variable de entorno, tratando el string vacío como ausente."""
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def env_required(name: str) -> str:
    """Igual que env(), pero aborta el arranque si falta.

    Se usa para los secretos: un default silencioso es peor que un fallo
    ruidoso, porque nadie revisa si el contenedor quedó con la clave de
    ejemplo hasta que alguien la usa para firmar una sesión.
    """
    value = env(name)
    if not value:
        raise RuntimeError(
            f"Falta la variable de entorno {name}. Superset no arranca sin ella. "
            f"Generá un valor con: python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    return value


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "on", "yes"}


def csv_env(name: str, default: Iterable[str] | None = None) -> list[str]:
    """Convierte una variable de entorno separada por comas en lista."""
    raw = env(name)
    if not raw:
        return list(default or [])
    return [chunk.strip() for chunk in raw.split(",") if chunk.strip()]


# ---------------------------------------------------------------------------
# Claves, backend de metadatos y caché
# ---------------------------------------------------------------------------

# Sin default a proposito. Antes: env("SUPERSET_SECRET_KEY", "change-this-in-prod")
# Con la SECRET_KEY conocida se pueden falsificar cookies de sesión.
# OJO al rotarla: Superset cifra con ella las credenciales de las conexiones
# guardadas. Antes de cambiarla, corré dentro del contenedor:
#     superset re-encrypt-secrets
SECRET_KEY = env_required("SUPERSET_SECRET_KEY")

SQLALCHEMY_DATABASE_URI = (
    "postgresql+psycopg2://"
    f"{env('POSTGRES_USER', 'superset')}:{env('POSTGRES_PASSWORD', '')}"
    f"@{env('POSTGRES_HOST', 'db')}:{env('POSTGRES_PORT', '5432')}/{env('POSTGRES_DB', 'superset')}"
)

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_HOST": env("REDIS_HOST", "redis"),
    "CACHE_REDIS_PORT": int(env("REDIS_PORT", "6379")),
    "CACHE_REDIS_DB": int(env("REDIS_CACHE_DB", "1")),
}

THUMBNAIL_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 86400,
    "CACHE_KEY_PREFIX": "superset_thumbnails_",
    "CACHE_REDIS_HOST": env("REDIS_HOST", "redis"),
    "CACHE_REDIS_PORT": int(env("REDIS_PORT", "6379")),
    "CACHE_REDIS_DB": int(env("REDIS_THUMBNAIL_DB", "3")),
}

# ---------------------------------------------------------------------------
# Celery / rate limits
# ---------------------------------------------------------------------------

from celery.schedules import crontab  # noqa: F401,E402  (Superset lo importa en runtime)


class CeleryConfig:
    broker_url = f"redis://{env('REDIS_HOST', 'redis')}:{env('REDIS_PORT', '6379')}/{env('REDIS_BROKER_DB', '0')}"
    result_backend = f"redis://{env('REDIS_HOST', 'redis')}:{env('REDIS_PORT', '6379')}/{env('REDIS_RESULTS_DB', '1')}"

    # Registrar tareas de thumbnails y cache
    imports = (
        "superset.sql_lab",
        "superset.tasks.thumbnails",
        "superset.tasks.cache",
    )


CELERY_CONFIG = CeleryConfig
RATELIMIT_STORAGE_URI = f"redis://{env('REDIS_HOST', 'redis')}:{env('REDIS_PORT', '6379')}/{env('REDIS_RATELIMIT_DB', '2')}"

# ---------------------------------------------------------------------------
# Cookies / CSRF / Reverse proxy
# ---------------------------------------------------------------------------

# Superset corre detrás de Traefik con TLS, así que por defecto asumimos HTTPS.
# Si alguna vez se sirve por HTTP plano, poné SUPERSET_FORCE_HTTPS=0 — pero
# entonces las cookies viajan sin la marca Secure.
USE_HTTPS = env_bool("SUPERSET_FORCE_HTTPS", True)

# Antes estaban forzados a False con las líneas originales comentadas.
# Una cookie de sesión sin Secure puede filtrarse por una conexión HTTP.
SESSION_COOKIE_SECURE = USE_HTTPS
SESSION_COOKIE_HTTPONLY = True

# SameSite=None es obligatorio para que la cookie viaje en un iframe de otro
# dominio (embed), y el navegador solo la acepta junto con Secure. Sin embed,
# Lax es más estricto y suficiente.
SESSION_COOKIE_SAMESITE = "None" if (USE_HTTPS and env_bool("SUPERSET_ENABLE_EMBED", False)) else "Lax"

WTF_CSRF_ENABLED = True
WTF_CSRF_SSL_STRICT = False

PREFERRED_URL_SCHEME = "https" if USE_HTTPS else "http"

APPLICATION_ROOT = "/"
SESSION_COOKIE_PATH = APPLICATION_ROOT
WTF_CSRF_COOKIE_PATH = APPLICATION_ROOT

ENABLE_PROXY_FIX = True
PROXY_FIX_CONFIG = {"x_for": 1, "x_host": 1, "x_port": 1, "x_proto": 1}

SUPERSET_WEBSERVER_TIMEOUT = int(env("SUPERSET_WEBSERVER_TIMEOUT", "120"))

LANGUAGES = {
    "en": {"flag": "us", "name": "English"},
    "es": {"flag": "es", "name": "Spanish"},
}

# Talisman se mantiene desactivado porque abajo servimos nuestro propio CSP.
TALISMAN_ENABLED = False

# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

# EMBEDDED_SUPERSET sigue activo por compatibilidad. Si confirmás que ya nadie
# embebe dashboards, ponelo en False: sin guest tokens desaparece toda la
# superficie de ataque del embed (los puntos 1 y 2 de la revisión).
FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": env_bool("SUPERSET_ENABLE_EMBED", True),
    "ENABLE_TEMPLATE_PROCESSING": True,
    "HORIZONTAL_FILTER_BAR": True,
    # Miniaturas DESACTIVADAS (1-sep-2026). Cada una lanza un Chrome que
    # vuelve a pedirle el dashboard a Superset, y necesita autenticarse:
    # sin THUMBNAIL_SELENIUM_USER definido se cuelga para siempre. Medido:
    # 10 tareas recibidas, 0 completadas, 0 fallidas en 30 min. Cada una
    # deja una peticion abierta en el navegador, y como HTTP/1.1 permite
    # unas 6 conexiones por servidor, el resto de la interfaz queda en cola
    # detras y parece caida. Para reactivarlas hace falta configurar un
    # usuario de servicio para el navegador headless.
    "THUMBNAILS": env_bool("SUPERSET_ENABLE_THUMBNAILS", False),
    "THUMBNAILS_SQLA_LISTENERS": env_bool("SUPERSET_ENABLE_THUMBNAILS", False),
    "ENABLE_DASHBOARD_SCREENSHOT_ENDPOINTS": env_bool("SUPERSET_ENABLE_THUMBNAILS", False),
    "ALERT_REPORTS": True,
    "AG_GRID_TABLE_ENABLED": True,
}

# ---------------------------------------------------------------------------
# Screenshots / reportes por correo
# ---------------------------------------------------------------------------

SUPERSET_PUBLIC_URL = env("SUPERSET_PUBLIC_URL", "https://superset.maryun.cl/")

WEBDRIVER_BASEURL = env("WEBDRIVER_BASEURL", "http://superset:8088/")
# Antes apuntaba a 68.211.136.20, una IP de otro despliegue. Es la URL que
# aparece en los links de los reportes que llegan por correo.
WEBDRIVER_BASEURL_USER_FRIENDLY = SUPERSET_PUBLIC_URL

WEBDRIVER_TYPE = "chrome"

WEBDRIVER_OPTION_ARGS = [
    "--headless=new",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-extensions",
    "--window-size=1920,2000",
]

SCREENSHOT_LOCATE_WAIT = 60
SCREENSHOT_LOAD_WAIT = 60

# Formato chileno: punto para miles, coma para decimales.
D3_FORMAT = {
    "decimal": ",",
    "thousands": ".",
    "grouping": [3],
    "currency": ["", ""],
}

# ---------------------------------------------------------------------------
# Embedded dashboards
# ---------------------------------------------------------------------------

# Sin default. Con este secreto se pueden FABRICAR guest tokens válidos, así
# que un valor de ejemplo equivale a dejar la puerta abierta.
GUEST_TOKEN_JWT_SECRET = env_required("GUEST_TOKEN_JWT_SECRET")

# El audience tiene que coincidir con la URL desde donde se embebe. Antes
# apuntaba a 190.21.87.72:7150, de otro proyecto.
GUEST_TOKEN_JWT_AUDIENCE = env("GUEST_TOKEN_JWT_AUDIENCE", SUPERSET_PUBLIC_URL)

# ANTES: "Admin" — cada token de invitado era administrador de la instancia.
# Este rol hay que crearlo en Security > Roles con permisos de solo lectura:
#   can read on Dashboard / Chart / Dataset / Database,
#   can explore json on Superset, can csrf token on Superset.
GUEST_ROLE_NAME = env("GUEST_ROLE_NAME", "Gamma")

# Vacío por defecto. Se declara con la variable de entorno EMBED_ALLOWED_ORIGINS,
# separando por comas y SOLO con los orígenes que se usen de verdad, por ejemplo:
#   EMBED_ALLOWED_ORIGINS="https://erp.maryun.cl,https://app.maryun.cl"
EMBED_ALLOWED_ORIGINS = csv_env("EMBED_ALLOWED_ORIGINS", [])

if EMBED_ALLOWED_ORIGINS:
    _ancestors = " ".join(EMBED_ALLOWED_ORIGINS)
    frame_ancestors = f"frame-ancestors 'self' {_ancestors};"
else:
    # Sin orígenes declarados, nadie puede embeber. Antes el "último recurso"
    # era frame-ancestors * (permitir a todo internet), que es justo lo que
    # no querés que pase por olvidarte de configurar una variable.
    frame_ancestors = "frame-ancestors 'self';"

# Se eliminó "X-Frame-Options: ALLOWALL". El CSP de arriba es el que gobierna
# quién puede embeber, y ALLOWALL lo contradecía.
OVERRIDE_HTTP_HEADERS = {
    "Content-Security-Policy": frame_ancestors,
}

# ---------------------------------------------------------------------------
# MCP (Model Context Protocol) — servidor nativo de Superset 5.x
# ---------------------------------------------------------------------------

# Por defecto EXIGE autenticación. Antes estaba en False, protegido solo por
# que el puerto 5008 no estuviera publicado: una medida que se pierde el día
# que alguien lo expone tras un proxy.
#
# Para acceso por túnel SSH desde localhost podés desactivarlo con
# MCP_AUTH_ENABLED=0, pero NO lo dejes así si el puerto sale a internet.
MCP_AUTH_ENABLED = env_bool("MCP_AUTH_ENABLED", True)
MCP_DEV_USERNAME = env("MCP_DEV_USERNAME", "maryun_admin")

if MCP_AUTH_ENABLED:
    MCP_JWT_ALGORITHM = env("MCP_JWT_ALGORITHM", "HS256")
    MCP_JWT_SECRET = env_required("MCP_JWT_SECRET")
    MCP_JWT_ISSUER = env("MCP_JWT_ISSUER", SUPERSET_PUBLIC_URL)
    MCP_JWT_AUDIENCE = env("MCP_JWT_AUDIENCE", "superset-mcp")

# ---------------------------------------------------------------------------
# Variables de entorno que este archivo espera
# ---------------------------------------------------------------------------
#
# Obligatorias (el arranque falla sin ellas):
#   SUPERSET_SECRET_KEY        python3 -c "import secrets; print(secrets.token_urlsafe(48))"
#   GUEST_TOKEN_JWT_SECRET     idem
#   MCP_JWT_SECRET             idem  (solo si MCP_AUTH_ENABLED=1)
#
# Opcionales:
#   SUPERSET_PUBLIC_URL        default https://superset.maryun.cl/
#   SUPERSET_FORCE_HTTPS       default 1
#   SUPERSET_ENABLE_EMBED      default 1 — ponelo en 0 si ya nadie embebe
#   EMBED_ALLOWED_ORIGINS      default vacío (nadie puede embeber)
#   GUEST_ROLE_NAME            default Embedded
#   MCP_AUTH_ENABLED           default 1
#   POSTGRES_* / REDIS_*       backend de metadatos y caché
