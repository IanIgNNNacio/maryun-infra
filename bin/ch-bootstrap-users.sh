#!/bin/bash
# Genera los usuarios de ClickHouse con contrasenas aleatorias.
# Las contrasenas NUNCA se imprimen: quedan en /srv/secrets/clickhouse.env (0640).
# Solo los hashes SHA256 van al XML.
set -euo pipefail

USERS_D="/srv/stacks/clickhouse/users.d"
SECRETS="/srv/secrets/clickhouse.env"

if [ -f "$SECRETS" ]; then
    echo "ERROR: $SECRETS ya existe. Si quieres regenerar, muevelo antes."
    exit 1
fi

gen() { openssl rand -base64 24 | tr -d '/+=' | head -c 28; }
sha() { printf '%s' "$1" | sha256sum | awk '{print $1}'; }

P_ADMIN="$(gen)"; P_MAGE="$(gen)"; P_BI="$(gen)"; P_MCP="$(gen)"

umask 077
cat > "$SECRETS" <<EOF
# ClickHouse — maryun01. Generado automaticamente, no versionar.
CLICKHOUSE_ADMIN_USER=admin
CLICKHOUSE_ADMIN_PASSWORD=$P_ADMIN
CLICKHOUSE_MAGE_USER=mage
CLICKHOUSE_MAGE_PASSWORD=$P_MAGE
CLICKHOUSE_BI_USER=bi
CLICKHOUSE_BI_PASSWORD=$P_BI
CLICKHOUSE_MCP_USER=mcp
CLICKHOUSE_MCP_PASSWORD=$P_MCP
EOF
chown root:maryun "$SECRETS"
chmod 0640 "$SECRETS"

cat > "$USERS_D/01-maryun.xml" <<EOF
<clickhouse>
    <profiles>
        <!-- readonly=2: puede leer y ajustar settings de sesion, pero no escribir. -->
        <lectura>
            <readonly>2</readonly>
            <max_memory_usage>8000000000</max_memory_usage>
            <max_execution_time>300</max_execution_time>
        </lectura>
        <escritura>
            <max_memory_usage>16000000000</max_memory_usage>
        </escritura>
    </profiles>

    <users>
        <!-- default queda encerrado en el contenedor: lo usa el healthcheck. -->
        <default>
            <networks><ip>127.0.0.1</ip><ip>::1</ip></networks>
            <profile>default</profile>
            <quota>default</quota>
        </default>

        <admin>
            <password_sha256_hex>$(sha "$P_ADMIN")</password_sha256_hex>
            <networks><ip>::/0</ip></networks>
            <profile>default</profile>
            <quota>default</quota>
            <access_management>1</access_management>
            <named_collection_control>1</named_collection_control>
        </admin>

        <mage>
            <password_sha256_hex>$(sha "$P_MAGE")</password_sha256_hex>
            <networks><ip>::/0</ip></networks>
            <profile>escritura</profile>
            <quota>default</quota>
        </mage>

        <bi>
            <password_sha256_hex>$(sha "$P_BI")</password_sha256_hex>
            <networks><ip>::/0</ip></networks>
            <profile>lectura</profile>
            <quota>default</quota>
        </bi>

        <mcp>
            <password_sha256_hex>$(sha "$P_MCP")</password_sha256_hex>
            <networks><ip>::/0</ip></networks>
            <profile>lectura</profile>
            <quota>default</quota>
        </mcp>
    </users>
</clickhouse>
EOF
chown root:maryun "$USERS_D/01-maryun.xml"
chmod 0644 "$USERS_D/01-maryun.xml"

echo "Usuarios definidos: admin (gestion), mage (escritura), bi (lectura), mcp (lectura)"
echo "Contrasenas en $SECRETS (0640 root:maryun). No se imprimieron."
echo "El XML solo contiene hashes SHA256."
