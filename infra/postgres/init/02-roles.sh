#!/bin/bash
# Logins besides the owner, and the credentials of every login. Table grants come from the Alembic baseline.
# Idempotent: runs on first start, and again from `make roles` so an existing volume follows .env.
#
#   bidwright_app     the API. Reads and writes rows; row-level security limits it to one workspace per transaction.
#   bidwright_studio  Prisma client and Studio, for developers. Rows only, no schema changes; sees every workspace.
#
# Values come from .env through docker-compose and reach Postgres as psql variables, never as text in this file.
set -euo pipefail
for name in POSTGRES_PASSWORD BIDWRIGHT_DB_PASSWORD BIDWRIGHT_APP_PASSWORD BIDWRIGHT_STUDIO_PASSWORD DB_POSTGRESDB_PASSWORD; do
  [ -n "${!name:-}" ] || { echo "$name is missing; run make env, then make db" >&2; exit 1; }
done
psql -v ON_ERROR_STOP=1 -U postgres \
  -v for_postgres="$POSTGRES_PASSWORD" -v for_owner="$BIDWRIGHT_DB_PASSWORD" -v for_app="$BIDWRIGHT_APP_PASSWORD" \
  -v for_studio="$BIDWRIGHT_STUDIO_PASSWORD" -v for_n8n="$DB_POSTGRESDB_PASSWORD" <<'SQL'
SELECT 'CREATE ROLE bidwright_app LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidwright_app') \gexec
SELECT 'CREATE ROLE bidwright_studio LOGIN BYPASSRLS' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidwright_studio') \gexec

CREATE FUNCTION pg_temp.set_login(login text, credential text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format('ALTER ROLE %I PASSWORD %L', login, credential);
END $$;

SELECT pg_temp.set_login('postgres', :'for_postgres');
SELECT pg_temp.set_login('bidwright', :'for_owner');
SELECT pg_temp.set_login('bidwright_app', :'for_app');
SELECT pg_temp.set_login('bidwright_studio', :'for_studio');
SELECT pg_temp.set_login('n8n', :'for_n8n');
GRANT CONNECT ON DATABASE bidwright TO bidwright_app, bidwright_studio;
SQL
