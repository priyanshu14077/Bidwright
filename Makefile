# SOG RFP platform: one entry point for setup and running.
#
#   make setup    first time, or after pulling changes: database, migrations, seed, dependencies
#   make dev      run API (8000), review UI (5173) and Prisma Studio (5555) together; Ctrl-C stops all
#   make status   which migration is applied and how many rows each table holds
#
# Every target is safe to run again.

SHELL := /bin/bash
DB    := docker compose exec -T postgres
PSQL  := $(DB) psql -U sog -d sog

.PHONY: setup env db wait roles migrate seed deps dev api web studio status psql eval eval-one check test reset stop

setup: env db wait roles deps migrate seed status

env:
	@test -f .env || (cp .env.example .env && echo "Created .env; set ANTHROPIC_API_KEY in it before extracting.")

db:
	docker compose up -d postgres

wait:
	@echo "Waiting for Postgres..."
	@until $(DB) pg_isready -U postgres -q; do sleep 1; done
	@until $(DB) psql -U postgres -tAc "select 1 from pg_database where datname='sog'" | grep -q 1; do sleep 1; done

# The init scripts run only on an empty volume; re-running the role script keeps an existing volume in step.
roles:
	@$(DB) bash < infra/postgres/init/02-prisma-role.sh > /dev/null && echo "Role sog_prisma ready"

migrate:
	cd service && uv run alembic upgrade head

seed:
	cd service && uv run python -m sog.archive.loader

deps:
	cd service && uv sync -q
	cd frontend && pnpm install --silent
	cd prisma && pnpm install --silent && pnpm run --silent generate

dev:
	@trap 'kill 0' INT TERM EXIT; \
	(cd service && uv run uvicorn sog.api:app --port 8000 --reload) & \
	(cd frontend && pnpm dev) & \
	(cd prisma && pnpm exec prisma studio --port 5555 --browser none) & \
	sleep 4; echo; echo "  Review UI      http://localhost:5173"; \
	echo "  API docs       http://localhost:8000/docs"; echo "  Prisma Studio  http://localhost:5555"; echo; \
	wait

api:
	cd service && uv run uvicorn sog.api:app --port 8000 --reload

web:
	cd frontend && pnpm dev

studio:
	cd prisma && pnpm exec prisma studio --port 5555

status:
	@$(PSQL) -tAc "select 'Applied migration: ' || version_num from public.alembic_version"
	@$(PSQL) -c "select s.nspname as schema, c.relname as table_name, \
	  (xpath('/row/n/text()', query_to_xml(format('select count(*) as n from %I.%I', s.nspname, c.relname), false, true, '')))[1]::text::int as rows \
	  from pg_class c join pg_namespace s on s.oid = c.relnamespace \
	  where c.relkind = 'r' and s.nspname in ('ref','archive','lineage','intake','eval') order by 1, 2"

psql:
	docker compose exec postgres psql -U sog -d sog

check:
	@cd service && uv run python -m sog.check

# One gold pack first (about 4 model calls), e.g. make eval-one CASE=P13
CASE ?= P13
eval-one:
	cd service && uv run python -m sog.evaluation --cases $(CASE)

eval:
	cd service && uv run python -m sog.evaluation $(ARGS)

stop:
	docker compose stop

test:
	cd service && uv run --group dev pytest -q

# Deletes the database volume and builds everything again from scratch.
reset:
	@read -p "Delete the sog and n8n databases and rebuild them? [y/N] " a && [ "$$a" = y ]
	docker compose down -v
	$(MAKE) setup
