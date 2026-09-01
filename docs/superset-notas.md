# Superset en maryun01 — dos trampas que costaron encontrar

## 1. No se podía iniciar sesión (HTTP plano + cookie `Secure`)

`superset_config.py` asume que Superset va detrás de Traefik con TLS:

```python
USE_HTTPS = env_bool("SUPERSET_FORCE_HTTPS", True)
SESSION_COOKIE_SECURE = USE_HTTPS
```

Pero en maryun01 se sirve por **HTTP plano en `10.8.0.1:8088`**, sin proxy
delante. Ningún navegador guarda una cookie marcada `Secure` recibida por HTTP.

La cadena completa:

1. El servidor responde `Set-Cookie: session=…; Secure; …` sobre `http://`
2. El navegador la descarta — `curl` lo dice sin rodeos:
   `* skipped cookie because not 'secure'`
3. Sin cookie de sesión, Flask-WTF no encuentra el token CSRF
4. Superset devuelve 302 al formulario, **anidando `next=` en cada vuelta**

**Para el usuario es indistinguible de "contraseña incorrecta", pero la
contraseña nunca llega a comprobarse** — ni siquiera se incrementa
`fail_login_count`.

### Cómo distinguir los dos fallos

Es la clave del diagnóstico. Mira a dónde redirige el `POST /login/`:

| Redirección | Significa |
|---|---|
| `/login/?next=/` | la contraseña **se evaluó** y se rechazó |
| `/login/?next=%2Flogin%2F…` anidándose | abortó en **CSRF**, por falta de cookie |

**Arreglo:** `SUPERSET_FORCE_HTTPS=0` en `/srv/secrets/superset.env`.

Contrapartida aceptada: la cookie viaja sin la marca `Secure`. Dentro de la VPN
el tráfico ya va cifrado, pero **si algún día se expone el 8088 fuera de la VPN,
la sesión queda interceptable**. Mantener la publicación limitada a `10.8.0.1`,
o poner TLS delante y volver la variable a `1`.

---

## 2. El nombre `db` apuntaba a dos bases de datos distintas

Los compose de Superset y del ERP llaman **ambos `db`** a su servicio de
Postgres. Docker agrega el nombre del servicio como alias de red, así que al
compartir la red `data` el alias colisionó:

```
db  →  172.20.1.10  (superset-db)      6 de 12 consultas
db  →  172.20.1.3   (maryun-erp-db)    6 de 12 consultas
```

Superset usaba `env('POSTGRES_HOST', 'db')`, o sea que **la mitad de sus
conexiones nuevas iban a la base del ERP**. No dio la cara antes porque el grupo
de conexiones se establece al arrancar y se reutiliza: solo fallan las nuevas.

**Arreglo:** `POSTGRES_HOST=superset-db` en `/srv/secrets/superset.env`.

El ERP no estaba afectado: usa `maryun-erp-db` explícito y solo pertenece a la
red `coolify`.

> **Lección general:** al unir stacks en una red compartida, revisar las
> colisiones de alias. El nombre del servicio se convierte en alias de red, y
> nombres genéricos como `db`, `cache` o `redis` chocan en silencio — el DNS de
> Docker devuelve las dos direcciones y el cliente elige una al azar.

Comprobación rápida:

```bash
for i in $(seq 10); do docker exec <contenedor> getent hosts db; done | sort | uniq -c
```

---

## Pendiente

`SUPERSET_PUBLIC_URL` sigue apuntando a `https://superset.maryun.cl/`, el
dominio del VPS viejo. Afecta los enlaces de reportes y alertas, y el
*audience* de los guest tokens de embebido. Definirlo al decidir el host
definitivo.
