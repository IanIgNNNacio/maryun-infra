#!/usr/bin/env python3
"""Ejecuta las tarjetas del tablero de comparacion y contrasta los dos motores.

Comprueba dos cosas distintas:

  1. Que los numeros COINCIDEN. Si no coinciden, el espejo esta mal y hay que
     saberlo antes de usarlo para nada.
  2. Cuanto tarda cada motor. Se mide tres veces y se toma la mediana: la
     primera ejecucion de Postgres carga paginas a memoria y no representa el
     estado normal.

La clave de API entra por la entrada estandar.
"""
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

BASE = "http://10.8.0.1:3000"
TABLERO = 25
REPETICIONES = 3
clave = sys.stdin.read().strip()


def api(ruta, metodo="GET", cuerpo=None):
    req = urllib.request.Request(
        BASE + ruta, method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"x-api-key": clave, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        t = r.read().decode(errors="replace").strip()
        return r.status, (json.loads(t) if t.startswith(("{", "[", '"')) else t[:300])


s, tab = api("/api/dashboard/%s" % TABLERO)
tarjetas = [dc["card"] for dc in tab.get("dashcards", []) if dc.get("card")]

# Se emparejan por el nombre, que termina en el motor.
parejas = {}
for c in tarjetas:
    nombre = c["name"]
    if " · " not in nombre:
        continue
    metrica, motor = nombre.rsplit(" · ", 1)
    parejas.setdefault(metrica, {})[motor] = c["id"]


def ejecutar(idcard):
    """Devuelve (filas, segundos_mediana). Descarta la primera medicion."""
    tiempos = []
    filas = None
    for i in range(REPETICIONES + 1):
        t0 = time.time()
        s, r = api("/api/card/%s/query" % idcard, "POST", {})
        t1 = time.time()
        if i > 0:
            tiempos.append(t1 - t0)
        datos = (r.get("data") or {}) if isinstance(r, dict) else {}
        filas = datos.get("rows")
    return filas, statistics.median(tiempos)


print("  %-42s %-14s %-14s %-10s" % ("metrica", "ClickHouse", "Postgres", "coinciden"))
print("  " + "-" * 84)

iguales = 0
distintos = []
for metrica, motores in parejas.items():
    if "ClickHouse" not in motores or "Postgres" not in motores:
        continue
    fch, tch = ejecutar(motores["ClickHouse"])
    fpg, tpg = ejecutar(motores["Postgres"])

    # Se normaliza a texto porque un motor puede devolver 12345 y el otro
    # "12345.00": el valor es el mismo y la diferencia seria de formato.
    def norm(filas):
        if filas is None:
            return None
        salida = []
        for fila in filas:
            salida.append([
                ("%.0f" % float(v)) if isinstance(v, (int, float)) else str(v)
                for v in fila
            ])
        return salida

    ok = norm(fch) == norm(fpg)
    if ok:
        iguales += 1
    else:
        distintos.append((metrica, fch, fpg))

    print("  %-42s %6.2fs        %6.2fs        %s"
          % (metrica[:42], tch, tpg, "si" if ok else "NO"))

print("")
print("  %d de %d metricas coinciden exactamente" % (iguales, len(parejas)))
for metrica, a, b in distintos:
    print("    %s" % metrica)
    print("      ClickHouse: %s" % str(a)[:180])
    print("      Postgres:   %s" % str(b)[:180])
