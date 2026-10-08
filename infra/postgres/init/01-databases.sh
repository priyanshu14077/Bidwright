#!/bin/bash
# Runs once, on first start of an empty volume. Passwords come from .env through docker-compose.
set -euo pipefail
: "${BIDWRIGHT_DB_PASSWORD:?missing; run make env}"
: "${N8N_DB_PASSWORD:?missing; run make env}"
psql -v ON_ERROR_STOP=1 -U postgres -v owner_pw="$BIDWRIGHT_DB_PASSWORD" -v n8n_pw="$N8N_DB_PASSWORD" <<'SQL'
CREATE ROLE bidwright LOGIN PASSWORD :'owner_pw';
CREATE DATABASE bidwright OWNER bidwright;
CREATE ROLE n8n LOGIN PASSWORD :'n8n_pw';
CREATE DATABASE n8n OWNER n8n;
REVOKE CONNECT ON DATABASE bidwright FROM PUBLIC;
REVOKE CONNECT ON DATABASE n8n FROM PUBLIC;
GRANT CONNECT ON DATABASE bidwright TO bidwright;
GRANT CONNECT ON DATABASE n8n TO n8n;
SQL
psql -v ON_ERROR_STOP=1 -U postgres -d bidwright -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
