# Bidwright

RFP intake and pricing for architecture and design practices. Bidwright reads the RFP packs a practice receives, cites every extracted value to a line on a page, flags where documents disagree, enriches the record from the practice's own reference data and past proposals, and backtests itself against a gold set.

Each practice works in its own workspace. Postgres row-level security keeps every workspace's rows apart; people join by invitation with one of four roles. Design and decisions: [`docs/revamp-plan.md`](docs/revamp-plan.md).

## Layout

| Path | What it is |
|---|---|
| `service/` | Python service: FastAPI, Alembic migrations, tenancy and auth, extraction, reference engine, backtesting |
| `frontend/` | Landing page (`/`) and the app (`/app/`): React, Vite, pdf.js |
| `data/archive/v0-synthetic/` | Northbeam Studio, the fictional demo practice: 14 past proposals |
| `data/gold/v0-synthetic/` | Northbeam's gold set: 14 RFP packs with expected values |
| `data/reference/v0.json` | Shared vocabulary, countries and FX, plus the starter pack every new workspace gets |
| `data/schema/request-v0.json` | The request schema. The extraction prompt, output schema and review form all derive from it |
| `infra/postgres/init/` | Creates the `bidwright` and `n8n` databases and the app and Studio logins on first start |
| `prisma/` | Prisma client and Studio over the `bidwright` database (introspection only) |

## Run it

Step-by-step guide: [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

Prerequisites: Docker Desktop running, `uv`, `pnpm`, LibreOffice (`brew install --cask libreoffice`).

```bash
make setup    # .env with random database passwords, Postgres container, roles, dependencies, migrations, seed
make dev      # API :8000, landing page and app :5173, Prisma Studio :5555 (Ctrl-C stops all three)
```

Open http://localhost:5173 for the landing page and http://localhost:5173/app/ for the app. The demo workspace signs in with `demo@northbeam.example` / `northbeam-demo`, or create your own workspace from the sign-up page.

| Command | What it does |
|---|---|
| `make setup` | Everything needed before running. Safe to repeat; the seed converges to the same rows |
| `make migrate` | `alembic upgrade head` only |
| `make seed` | Reload shared data and the demo workspace only |
| `make status` | Applied migration, workspaces, and row count per table |
| `make dev` | All three services in one terminal |
| `make api` / `make web` / `make studio` | One service each, for separate terminals |
| `make psql` | Open psql on the `bidwright` database as the owner |
| `make check` | Preflight: database, migrations, demo workspace, API key, LibreOffice, services |
| `make eval-one CASE=P13` | Backtest one gold pack of the demo workspace |
| `make eval` | Backtest all 14 (`ARGS=--rescore` to re-score without model calls; `ARGS="--workspace <slug>"` for another workspace) |
| `make test` | Engine rules and workspace isolation tests |
| `make stop` | Stop the database container (data is kept) |
| `make reset` | Delete the database volume and rebuild from scratch (asks first) |

n8n, optional: `docker compose --profile n8n up -d` (port 5679, its own database).

## The database

One Postgres server (Docker, port 5434) holds two databases: `bidwright` and `n8n` (n8n's own state).

| Schema | Holds |
|---|---|
| `tenancy` | Workspaces, users, memberships, sessions, invitations |
| `ref` | Shared: vocabulary, countries, FX, schema definitions. Per workspace: synonyms, markets and tiers, studios, bid rules, field weights, fee assumptions, benchmarks |
| `archive` | Past proposals: fees by line, payments, deliverables, T&C deviations, sections, dataset versions |
| `lineage` | Every source file, its pages and layout lines, every extracted value, the append-only change log |
| `intake` | RFP envelopes, extraction runs, model calls, conflicts, engine flags, confirmed records |
| `eval` | Gold cases and every backtest run |

Every per-workspace table has `org_id`, filled in from the transaction's `app.org_id` setting, and a row-level security policy on it. A transaction without a workspace sees no rows and cannot insert any.

Logins. Each password is a random value that `make env` writes to `.env`, which git ignores. No password is stored in a tracked file.

| Login | Password in `.env` | Can | Use it for |
|---|---|---|---|
| `bidwright` | `BIDWRIGHT_DB_PASSWORD` | Owns the schemas; still bound by row-level security | Alembic and the seed |
| `bidwright_app` | `BIDWRIGHT_APP_PASSWORD` | Read and write rows in one workspace at a time; read-only on the shared vocabulary | The API |
| `bidwright_studio` | `BIDWRIGHT_STUDIO_PASSWORD` | Read and write rows in every workspace, no schema changes | Prisma Studio and GUI tools, development only |
| `postgres` | `POSTGRES_SUPERUSER_PASSWORD` | Superuser | Administration only |

To change a password, edit it in `.env` and run `make db roles`, then restart `make dev`.

```bash
make psql                      # owner login; set a workspace first to see its rows:
#   select set_config('app.org_id', (select org_id::text from tenancy.organization where slug = 'northbeam'), false);
#   select reference, title, status from archive.proposal;

# any GUI (TablePlus, DBeaver, DataGrip, pgAdmin): host localhost, port 5434, database bidwright,
#   user bidwright_studio, password: BIDWRIGHT_STUDIO_PASSWORD from .env
```

Never run `prisma migrate` or `prisma db push`. The Studio login cannot change the schema, so Postgres refuses them. After a migration, run `cd prisma && pnpm run pull` to refresh `schema.prisma`.

## Access

| Role | Can |
|---|---|
| Owner | Everything, including appointing owners and admins |
| Admin | Invite people, edit reference data and bid rules, run backtests, read model call logs |
| Estimator | Upload RFP packs, correct values, resolve conflicts, confirm records |
| Viewer | Read every record, source and backtest |

Every API endpoint asks for one permission (`bidwright/tenancy.py`, `PERMISSIONS`) through `require()` in `bidwright/auth.py`, which also puts the request in the caller's workspace. Sessions are httpOnly cookies; only a hash of the token is stored. Invitations are single-use links tied to an email address; there is no email delivery yet, so the inviter copies the link.

## Backtesting

```bash
make eval-one CASE=P13                                     # one pack, about 4 model calls
make eval                                                  # all 14
cd service && uv run python -m bidwright.evaluation --rescore   # score the last run again without model calls
```

Each run is stored in `eval.run`, `eval.case_result` and `eval.field_result` in the workspace, with a readable report in `data/gold/v0-synthetic/runs/<timestamp>.md`. The Backtests page shows the latest run.

## Rules for changing the data shape

- The Python service owns the database shape. Change it only with a new Alembic migration in `service/migrations/versions/`.
- Write migrations by hand. Autogenerate is switched off (no target metadata).
- A new per-workspace table needs `org_id` with the default from `tenancy.current_org()`, `org_id` in its natural keys, and the row-level security policy: add it to `TENANT_TABLES` in the same way as `0001_baseline.py`. `tests/test_tenancy.py` is the check.
- `lineage.field_change` and `intake.opportunity` are append-only; a trigger rejects updates and deletes.
