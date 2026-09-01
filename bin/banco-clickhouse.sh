#!/usr/bin/env bash
#
# Banco de pruebas de ClickHouse, para comparar el VPS viejo con maryun01.
#
# Mide el motor y la maquina, NO la red: se ejecuta dentro de cada servidor.
# Asi se separa "el servidor nuevo es mas rapido" de "la VPN anade latencia",
# que son dos preguntas distintas y se responden distinto.
#
# Cada consulta corre 4 veces: la primera se DESCARTA (paga leer de disco y
# llenar caches) y de las otras tres se toma la mediana. Un dashboard que se
# usa a diario trabaja en caliente, asi que esa es la cifra representativa; la
# primera se informa aparte porque tambien importa.
#
# Uso:  banco-clickhouse.sh [etiqueta]

set -uo pipefail

ETIQUETA="${1:-servidor}"
CORRIDAS=4
DIR=/tmp/banco-ch

mkdir -p "$DIR"

# ── las consultas ──────────────────────────────────────────────────────────
cat > "$DIR/q1.sql" <<'SQL'
SELECT count(), sum(montoTotal), uniqExact(rutProveedor)
FROM dwh.facturas_jp_mongo_history
SQL

cat > "$DIR/q2.sql" <<'SQL'
SELECT rutProveedor, periodo, count() AS c, sum(montoTotal) AS t
FROM dwh.facturas_jp_mongo_history
GROUP BY rutProveedor, periodo
ORDER BY t DESC
LIMIT 20
SQL

cat > "$DIR/q3.sql" <<'SQL'
SELECT count(), sum(total), sum(pagado)
FROM dwh.mysis_mstr_pedidos FINAL
SQL

cat > "$DIR/q4.sql" <<'SQL'
SELECT f.tipoDoc, count() AS c, sum(f.montoTotal) AS t
FROM dwh.facturas_jp_mongo_history AS f
INNER JOIN (
    SELECT rutProveedor
    FROM dwh.facturas_jp_mongo_history
    GROUP BY rutProveedor
    HAVING count() > 1000
) AS g ON f.rutProveedor = g.rutProveedor
GROUP BY f.tipoDoc
ORDER BY t DESC
LIMIT 10
SQL

cat > "$DIR/q5.sql" <<'SQL'
SELECT count(), uniqExact(folio)
FROM dwh.facturas_jp_mongo_history
WHERE razon_social ILIKE '%LTDA%'
SQL

DESCRIPCION=(
  "escaneo total 114M filas + cardinalidad"
  "GROUP BY alta cardinalidad + orden"
  "ReplacingMergeTree con FINAL"
  "JOIN con lado de construccion grande"
  "filtro de texto sobre 114M filas"
)

# ── el motor de medicion ───────────────────────────────────────────────────
# clickhouse-client imprime el tiempo transcurrido en stderr con --time.
correr() {
    docker exec -i clickhouse clickhouse-client --time --queries-file "/tmp/banco/$1" \
        >/dev/null 2>"$DIR/tiempo.txt"
    tail -1 "$DIR/tiempo.txt" | tr -d '\r'
}

docker exec clickhouse mkdir -p /tmp/banco 2>/dev/null
for f in q1 q2 q3 q4 q5; do
    docker cp "$DIR/$f.sql" clickhouse:/tmp/banco/"$f.sql" >/dev/null 2>&1
done

echo "  === $ETIQUETA ==="
echo "  carga antes: $(cut -d' ' -f1-3 /proc/loadavg)"
echo "  version: $(docker exec clickhouse clickhouse-client --query 'SELECT version()' 2>/dev/null)"
echo "  hilos que usara: $(docker exec clickhouse clickhouse-client --query 'SELECT getSetting(\$\$max_threads\$\$)' 2>/dev/null || nproc)"
echo
printf '  %-3s %-42s %9s %9s %9s\n' "" "consulta" "1a (fria)" "mediana" "mejor"
printf '  %s\n' "--------------------------------------------------------------------------------"

i=0
for f in q1 q2 q3 q4 q5; do
    i=$((i + 1))
    tiempos=()
    for n in $(seq "$CORRIDAS"); do
        t="$(correr "$f.sql")"
        # El formato es "0.123 sec." — se queda el numero.
        t="$(echo "$t" | grep -oE '[0-9]+\.[0-9]+' | head -1)"
        [ -z "$t" ] && t=0
        tiempos+=("$t")
    done
    fria="${tiempos[0]}"
    calientes=$(printf '%s\n' "${tiempos[@]:1}" | sort -g)
    mediana="$(echo "$calientes" | sed -n 2p)"
    mejor="$(echo "$calientes" | head -1)"
    printf '  %-3s %-42s %8ss %8ss %8ss\n' "q$i" "${DESCRIPCION[$((i-1))]}" "$fria" "$mediana" "$mejor"
done

echo
echo "  carga despues: $(cut -d' ' -f1-3 /proc/loadavg)"
docker exec clickhouse rm -rf /tmp/banco 2>/dev/null
rm -rf "$DIR"
