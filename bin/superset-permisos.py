#!/usr/bin/env python3
"""Deja Superset con dos administradores y el resto en solo lectura.

Que arregla:

  1. vbecerra@gmail.com tenia rol Admin. Ultimo acceso el 2025-12-01, hace
     nueve meses, y con correo personal. Admin en Superset permite SQL Lab,
     ver las credenciales de conexion y cambiar cualquier cosa.

  2. El rol «only_read» no era de solo lectura: tenia can_write sobre
     Dashboard. El nombre enganaba.

  3. isabela tenia only_read Y Gamma. Gamma permite crear graficos y conjuntos
     de datos.

Se queda como esta, a proposito:
  - ian.alrringo@maryun.cl y felipe.delamaza@maryun.cl siguen siendo Admin.
  - cianignacios@gmail.com se queda en only_read; su nombre es «Embebido
    Superset», asi que puede estar sirviendo un tablero incrustado y quitarle
    el acceso lo romperia.

Se escribe en la base de Superset porque Flask-AppBuilder lee los roles de
ahi en cada peticion y no expone estos cambios por su API.

    sudo /srv/bin/superset-permisos.py            solo informa
    sudo /srv/bin/superset-permisos.py --hazlo    aplica
"""
import subprocess
import sys

HAZLO = "--hazlo" in sys.argv
ADMINS = ("ian.alrringo@maryun.cl", "felipe.delamaza@maryun.cl")


def sql(consulta, escribir=False):
    if escribir and not HAZLO:
        return "[simulacion]"
    r = subprocess.run(
        ["docker", "exec", "-i", "superset-db", "psql", "-U", "superset",
         "-d", "superset", "-X", "-tA", "-v", "ON_ERROR_STOP=1", "-c", consulta],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        # se imprime aqui mismo: la primera version devolvia el error como si
        # fuese un resultado y nadie lo miraba, asi que un INSERT fallido pasaba
        # por bueno
        print("   ERROR de SQL: %s" % r.stderr.strip()[:200])
        return "ERROR: " + r.stderr.strip()[:200]
    return r.stdout.strip()


def estado(titulo):
    print("\n== %s" % titulo)
    print(sql("""
        SELECT '   ' || u.email || '  ->  ' || coalesce(string_agg(r.name, ', ' ORDER BY r.name), 'sin rol')
        FROM ab_user u
        LEFT JOIN ab_user_role ur ON ur.user_id = u.id
        LEFT JOIN ab_role r ON r.id = ur.role_id
        GROUP BY u.id, u.email ORDER BY u.email"""))
    print("   ---")
    print(sql("""
        SELECT '   only_read tiene ' || count(*) || ' permiso(s) de escritura'
        FROM ab_role r
        JOIN ab_permission_view_role pvr ON pvr.role_id = r.id
        JOIN ab_permission_view pv ON pv.id = pvr.permission_view_id
        JOIN ab_permission p ON p.id = pv.permission_id
        WHERE r.name = 'only_read' AND p.name ~* 'write|edit|add|delete|save|dml|sqllab'"""))


estado("antes")

print("\n== cambios")

# 1 · quitar Admin a quien no sea Ian o Felipe, y dejarlo en only_read
sobran = sql("""
    SELECT u.email FROM ab_user u
    JOIN ab_user_role ur ON ur.user_id = u.id
    JOIN ab_role r ON r.id = ur.role_id
    WHERE r.name = 'Admin' AND u.email NOT IN ('%s','%s')""" % ADMINS)
for correo in [x for x in sobran.splitlines() if x.strip()]:
    print("   %s: Admin -> only_read" % correo)
    sql("""DELETE FROM ab_user_role
           WHERE user_id = (SELECT id FROM ab_user WHERE email = '%s')
             AND role_id = (SELECT id FROM ab_role WHERE name = 'Admin')""" % correo, True)
    # ab_user_role.id es not null SIN valor por defecto: la secuencia existe
    # pero no esta enganchada a la columna, porque SQLAlchemy pide el nextval
    # por su cuenta. Sin esto el INSERT falla y el usuario queda SIN NINGUN rol,
    # que es peor que dejarlo como estaba.
    sql("""INSERT INTO ab_user_role (id, user_id, role_id)
           SELECT nextval('ab_user_role_id_seq'), u.id, r.id FROM ab_user u, ab_role r
           WHERE u.email = '%s' AND r.name = 'only_read'
             AND NOT EXISTS (SELECT 1 FROM ab_user_role x
                             WHERE x.user_id = u.id AND x.role_id = r.id)""" % correo, True)

# 2 · quitar Gamma y Alpha a los que no son administradores
con_gamma = sql("""
    SELECT u.email || '|' || r.name FROM ab_user u
    JOIN ab_user_role ur ON ur.user_id = u.id
    JOIN ab_role r ON r.id = ur.role_id
    WHERE r.name IN ('Gamma','Alpha','sql_lab')
      AND u.email NOT IN ('%s','%s')""" % ADMINS)
for fila in [x for x in con_gamma.splitlines() if "|" in x]:
    correo, rol = fila.split("|")
    print("   %s: se le quita el rol %s" % (correo, rol))
    sql("""DELETE FROM ab_user_role
           WHERE user_id = (SELECT id FROM ab_user WHERE email = '%s')
             AND role_id = (SELECT id FROM ab_role WHERE name = '%s')""" % (correo, rol), True)

# 3 · only_read no puede escribir, que para eso se llama asi
escribe = sql("""
    SELECT p.name || ' -> ' || vm.name
    FROM ab_role r
    JOIN ab_permission_view_role pvr ON pvr.role_id = r.id
    JOIN ab_permission_view pv ON pv.id = pvr.permission_view_id
    JOIN ab_permission p ON p.id = pv.permission_id
    JOIN ab_view_menu vm ON vm.id = pv.view_menu_id
    WHERE r.name = 'only_read' AND p.name ~* 'write|edit|add|delete|save|dml|sqllab'""")
for fila in [x for x in escribe.splitlines() if x.strip()]:
    print("   only_read pierde: %s" % fila)
sql("""
    DELETE FROM ab_permission_view_role pvr
    USING ab_role r, ab_permission_view pv, ab_permission p
    WHERE pvr.role_id = r.id AND pvr.permission_view_id = pv.id AND pv.permission_id = p.id
      AND r.name = 'only_read' AND p.name ~* 'write|edit|add|delete|save|dml|sqllab'""", True)

if HAZLO:
    estado("despues")
    quedan = sql("""
        SELECT count(*) FROM ab_user u
        JOIN ab_user_role ur ON ur.user_id = u.id
        JOIN ab_role r ON r.id = ur.role_id
        WHERE r.name = 'Admin' AND u.email NOT IN ('%s','%s')""" % ADMINS)
    print("\n   administradores que no son Ian ni Felipe: %s" % quedan)
