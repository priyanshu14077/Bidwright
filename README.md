# SOG RFP Intelligence Platform: Phase 1 proof of concept

Intake, extraction with page citations, the reference engine, and the review and audit frontend. Plan and decisions: `docs/poc-plan.md`.

## Layout

| Path | What it is |
|---|---|
| `service/` | Python service: FastAPI, Alembic migrations, extraction, reference engine, evaluation |
| `frontend/` | Review UI (React, Vite, pdf.js) |
| `data/archive/v0-synthetic/` | Synthetic proposal archive: 14 proposals, JSON ground truth, original SQL |
| `data/gold/v0-synthetic/` | Generated RFP packs, `truth.json`, evaluation runs |
| `data/reference/v0.json` | Markets, location tiers, FX, controlled terms, synonyms, qualification rules |
| `data/schema/request-v0.json` | The proposal request schema. The extraction prompt, output schema and review form all derive from it |
| `infra/postgres/init/` | Creates the `sog` and `n8n` databases and the `sog_prisma` login on first start |
| `prisma/` | Prisma client and Studio over the `sog` database (introspection only) |

## Run it

Step-by-step guide: [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

Prerequisites: Docker Desktop running, `uv`, `pnpm`, LibreOffice (`brew install --cask libreoffice`).

```bash
make setup    # 1. .env  2. Postgres container  3. roles  4. dependencies  5. migrations  6. seed  7. status
make dev      # API :8000, review UI :5173, Prisma Studio :5555 (Ctrl-C stops all three)
```

| Command | What it does |
|---|---|
| `make setup` | Everything needed before running. Safe to repeat; the seed converges to the same rows |
| `make migrate` | `alembic upgrade head` only |
| `make seed` | Reload reference data, archive and gold packs only |
| `make status` | Applied migration and row count per table |
| `make dev` | All three services in one terminal |
| `make api` / `make web` / `make studio` | One service each, for separate terminals |
| `make psql` | Open psql on the `sog` database |
| `make check` | Preflight: database, migrations, seed, API key, LibreOffice, services |
| `make eval-one CASE=P13` | Extract and score one gold pack |
| `make eval` | Extract and score all 14 gold packs (`ARGS=--rescore` to re-score without model calls) |
| `make stop` | Stop the database container (data is kept) |
| `make test` | Engine unit tests |
| `make reset` | Delete the database volume and rebuild from scratch (asks first) |

n8n, optional: `docker compose --profile n8n up -d` (port 5679, its own database).

## The database

One Postgres server (Docker, port 5434) holds two databases: `sog` (ours) and `n8n` (n8n's own state).

| Schema | Holds |
|---|---|
| `ref` | Controlled terms, synonyms, markets and tiers, FX, fee assumptions, qualification rules, schema definitions |
| `archive` | Past proposals: fees by line, payments, deliverables, T&C deviations, sections, dataset versions |
| `lineage` | Every source file (RFP or archive), its pages and layout lines, every extracted value, the append-only change log |
| `intake` | RFP envelopes, extraction runs, model calls, conflicts, engine flags, confirmed opportunities |
| `eval` | Gold cases (pack, expected values, planted conflicts) and every evaluation run |

Logins:

| Login | Can | Use it for |
|---|---|---|
| `sog` / `sog` | Everything, owns the schemas | The Python service and Alembic |
| `sog_prisma` / `sog_prisma` | Read and write rows, no schema changes | Prisma client and Studio, GUI tools |
| `postgres` / `postgres` | Superuser | Administration only |

Ways in:

```bash
# psql inside the container
docker exec -it sog-postgres-1 psql -U sog -d sog
#   \dn                      list schemas
#   \dt archive.*            list tables in a schema
#   \d+ archive.proposal     describe a table
#   select reference, title, status from archive.proposal;

# any GUI (TablePlus, DBeaver, DataGrip, pgAdmin)
#   host localhost, port 5434, database sog, user sog_prisma, password sog_prisma

# Prisma Studio (browse and edit rows): http://localhost:5555
cd prisma && pnpm install && pnpm generate && pnpm studio

# Prisma client from TypeScript: see prisma/examples/query.ts
cd prisma && pnpm example
```

Changing the schema:

1. Write an Alembic migration in `service/migrations/versions/` and run `uv run alembic upgrade head`.
2. Refresh Prisma: `cd prisma && pnpm pull` (introspects the database and regenerates the client).

Never run `prisma migrate` or `prisma db push`. The `sog_prisma` login has no rights to change the schema, so Postgres refuses them anyway.

## Measure accuracy

```bash
cd service
uv run python -m sog.evaluation                  # ingests and extracts all 14 packs, then scores them
uv run python -m sog.evaluation --cases P02 P13  # a subset
uv run python -m sog.evaluation --rescore        # score the last run again without model calls
uv run --group dev pytest                        # engine rules
```

Each run is stored in `eval.run`, `eval.case_result` and `eval.field_result`, with a readable report in `data/gold/v0-synthetic/runs/<timestamp>.md`. The Accuracy page shows the latest run.

## Rules for changing the data shape

- The Python service owns the database shape. Change it only with a new Alembic migration in `service/migrations/versions/`.
- Write migrations by hand. Autogenerate is switched off (no target metadata) and is limited to the `ref`, `archive`, `lineage`, `intake` and `eval` schemas.
- `lineage.field_change` and `intake.opportunity` are append-only; a trigger rejects updates and deletes.
- A new archive dataset goes in `data/archive/<version>/` and is loaded with `--dataset <version>`. Every extraction run records the dataset version.
