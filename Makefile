# Bidwright: one entry point for setup and running.
#
#   make setup    first time, or after pulling changes: database, migrations, seed, dependencies
#   make dev      run API (8000), web app (5173) and Prisma Studio (5555) together; Ctrl-C stops all
#   make status   which migration is applied and how many rows each table holds
#
# Every target is safe to run again.

SHELL := /bin/bash
DB    := docker compose exec -T postgres
PSQL  := $(DB) psql -U bidwright -d bidwright

.PHONY: setup env db wait roles migrate seed deps dev api web studio status psql eval eval-one check test reset stop

setup: env db wait roles deps migrate seed status

PASSWORDS := POSTGRES_SUPERUSER_PASSWORD BIDWRIGHT_DB_PASSWORD BIDWRIGHT_APP_PASSWORD BIDWRIGHT_STUDIO_PASSWORD N8N_DB_PASSWORD

# Creates .env with a random value for every empty database password. An existing .env only gets its empty ones filled.
env:
	@test -f .env || { cp .env.example .env && echo "Created .env; set ANTHROPIC_API_KEY in it before extracting."; }
	@for k in $(PASSWORDS); do \
	  grep -q "^$$k=" .env || echo "$$k=" >> .env; \
	  sed -i.bak "s/^$$k=$$/$$k=$$(openssl rand -hex 16)/" .env; \
	done; rm -f .env.bak

# Recreates the container when .env changes; the data volume is kept.
db: env
	docker compose up -d postgres

wait:
	@echo "Waiting for Postgres..."
	@until $(DB) pg_isready -U postgres -q; do sleep 1; done
	@until $(DB) psql -U postgres -tAc "select 1 from pg_database where datname='bidwright'" | grep -q 1; do sleep 1; done

# The init scripts run only on an empty volume; re-running the role script keeps an existing volume in step.
roles:
	@$(DB) bash < infra/postgres/init/02-roles.sh > /dev/null && echo "Roles bidwright_app and bidwright_studio ready"

migrate:
	cd service && uv run alembic upgrade head

# Shared reference data, then the demo workspace (Northbeam Studio) with its sample archive and gold set
seed:
	cd service && uv run python -m bidwright.archive.loader

deps:
	cd service && uv sync -q
	cd frontend && pnpm install --silent
	cd prisma && pnpm install --silent && pnpm run --silent generate

dev:
	@trap 'kill 0' INT TERM EXIT; \
	(cd service && uv run uvicorn bidwright.api:app --port 8000 --reload) & \
	(cd frontend && pnpm dev) & \
	(cd prisma && pnpm exec prisma studio --port 5555 --browser none) & \
	sleep 4; echo; echo "  Landing page   http://localhost:5173"; \
	echo "  App            http://localhost:5173/app/  (demo: demo@northbeam.example / northbeam-demo)"; \
	echo "  API docs       http://localhost:8000/docs"; echo "  Prisma Studio  http://localhost:5555"; echo; \
	wait

api:
	cd service && uv run uvicorn bidwright.api:app --port 8000 --reload

web:
	cd frontend && pnpm dev

studio:
	cd prisma && pnpm exec prisma studio --port 5555

status:
	@$(PSQL) -tAc "select 'Applied migration: ' || version_num from public.alembic_version"
	@$(PSQL) -tAc "select 'Workspaces: ' || string_agg(name || case when is_demo then ' (demo)' else '' end, ', ') from tenancy.organization"
	@$(DB) psql -U postgres -d bidwright -c "select s.nspname as schema, c.relname as table_name, \
	  (xpath('/row/n/text()', query_to_xml(format('select count(*) as n from %I.%I', s.nspname, c.relname), false, true, '')))[1]::text::int as rows \
	  from pg_class c join pg_namespace s on s.oid = c.relnamespace \
	  where c.relkind = 'r' and s.nspname in ('tenancy','ref','archive','lineage','intake','eval') order by 1, 2"

psql:
	docker compose exec postgres psql -U bidwright -d bidwright

check:
	@cd service && uv run python -m bidwright.check

# Backtest one gold pack of the demo workspace first (about 4 model calls), e.g. make eval-one CASE=P13
CASE ?= P13
eval-one:
	cd service && uv run python -m bidwright.evaluation --cases $(CASE)

eval:
	cd service && uv run python -m bidwright.evaluation $(ARGS)

stop:
	docker compose stop

test:
	cd service && uv run --group dev pytest -q

# Deletes the database volume and builds everything again from scratch.
reset:
	@read -p "Delete the bidwright and n8n databases and rebuild them? [y/N] " a && [ "$$a" = y ]
	docker compose down -v
	$(MAKE) setup
