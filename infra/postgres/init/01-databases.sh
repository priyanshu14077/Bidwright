#!/bin/bash
# Runs once, on first start of an empty volume. Logins get their passwords in 02-roles.sh.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U postgres <<'SQL'
CREATE ROLE bidwright LOGIN;
CREATE DATABASE bidwright OWNER bidwright;
CREATE ROLE n8n LOGIN;
CREATE DATABASE n8n OWNER n8n;
REVOKE CONNECT ON DATABASE bidwright FROM PUBLIC;
REVOKE CONNECT ON DATABASE n8n FROM PUBLIC;
GRANT CONNECT ON DATABASE bidwright TO bidwright;
GRANT CONNECT ON DATABASE n8n TO n8n;
SQL
psql -v ON_ERROR_STOP=1 -U postgres -d bidwright -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
