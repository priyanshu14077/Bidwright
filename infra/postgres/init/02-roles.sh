#!/bin/bash
# Logins besides the owner. Table grants come from the Alembic baseline. Idempotent.
#
#   bidwright_app     the API. Reads and writes rows; row-level security limits it to one workspace per transaction.
#   bidwright_studio  Prisma client and Studio, for developers. Rows only, no schema changes; sees every workspace.
#
# Passwords come from .env through docker-compose. Every run sets them again, so changing a password in
# .env and running `make db roles` keeps an existing volume in step.
set -euo pipefail
: "${BIDWRIGHT_DB_PASSWORD:?missing; run make env}"
: "${BIDWRIGHT_APP_PASSWORD:?missing; run make env}"
: "${BIDWRIGHT_STUDIO_PASSWORD:?missing; run make env}"
: "${POSTGRES_PASSWORD:?missing; run make env}"
: "${N8N_DB_PASSWORD:?missing; run make env}"
psql -v ON_ERROR_STOP=1 -U postgres \
  -v owner_pw="$BIDWRIGHT_DB_PASSWORD" -v app_pw="$BIDWRIGHT_APP_PASSWORD" -v studio_pw="$BIDWRIGHT_STUDIO_PASSWORD" \
  -v super_pw="$POSTGRES_PASSWORD" -v n8n_pw="$N8N_DB_PASSWORD" <<'SQL'
SELECT 'CREATE ROLE bidwright_app LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidwright_app') \gexec
SELECT 'CREATE ROLE bidwright_studio LOGIN BYPASSRLS' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidwright_studio') \gexec
ALTER ROLE postgres PASSWORD :'super_pw';
ALTER ROLE n8n PASSWORD :'n8n_pw';
ALTER ROLE bidwright PASSWORD :'owner_pw';
ALTER ROLE bidwright_app PASSWORD :'app_pw';
ALTER ROLE bidwright_studio PASSWORD :'studio_pw';
GRANT CONNECT ON DATABASE bidwright TO bidwright_app, bidwright_studio;
SQL
