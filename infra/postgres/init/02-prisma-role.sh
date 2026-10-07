#!/bin/bash
# Login for Prisma (client and Studio): reads and writes rows, cannot change the
# schema, so `prisma migrate` / `db push` fail instead of fighting Alembic.
# Table grants are applied by Alembic migration 0004 and later. Idempotent.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U postgres <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sog_prisma') THEN
    CREATE ROLE sog_prisma LOGIN PASSWORD '${SOG_PRISMA_PASSWORD:-sog_prisma}';
  END IF;
END \$\$;
GRANT CONNECT ON DATABASE sog TO sog_prisma;
SQL
