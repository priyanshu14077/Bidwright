# Runbook: running Bidwright locally

Every command below runs from the **project root**, where the `Makefile` lives. Each `make` command moves into the right folder by itself.

You will use **two terminal tabs**:

- **Tab A, services:** runs `make dev` and stays busy. Leave it open.
- **Tab B, commands:** everything else.

---

## Part 1: One-time setup

Do this once on a new machine, or after `make reset`.

### Step 1. Check the tools

Open Docker Desktop and wait until it says it is running. Then in **Tab B**:

```bash
docker --version && uv --version && pnpm --version && ls /Applications/LibreOffice.app
```

All four should print something. If LibreOffice is missing: `brew install --cask libreoffice`.

### Step 2. Add the API key

1. Run `make env`. It creates `.env` from `.env.example` and fills every database password with a random value. An existing `.env` only gets its empty passwords filled.
2. On the line `ANTHROPIC_API_KEY=`, paste the key directly after the `=`, with no spaces or quotes.
3. **Save the file.** The services read `.env` from disk at start-up.

### Step 3. Build the database and install everything

In **Tab B**:

```bash
make setup
```

This starts the Postgres container `bidwright-postgres-1`, creates the logins, installs the Python, frontend and Prisma dependencies, applies the migration, and seeds the shared data and the demo workspace. It takes about a minute. It ends by printing the workspaces (`Northbeam Studio (demo)`) and a row count per table; `archive | proposal` should show `14`.

### Step 4. Check everything

```bash
make check
```

Every line under Database, Model and Tools must say `OK`. If `API key` says FAIL, go back to Step 2 and save the file.

---

## Part 2: Every working session

### Step 5. Start the database (if Docker was restarted)

```bash
make db
```

This does nothing if the database is already running.

### Step 6. Start the services

In **Tab A**:

```bash
make dev
```

Wait for the addresses to print, then open them:

| Address | What it is |
|---|---|
| http://localhost:5173 | Landing page |
| http://localhost:5173/app/ | The app: inbox, review, backtests, reference data, members, model calls |
| http://localhost:8000/docs | API (sign in through the app first; the session cookie is shared) |
| http://localhost:5555 | Prisma Studio: browse every table in every workspace |

The web app needs port 5173 free. If something else holds it, `make dev` fails instead of moving to another port; stop the other process (`lsof -iTCP:5173 -sTCP:LISTEN`) and run it again.

**Restart `make dev` (Ctrl-C, then `make dev`) whenever you change `.env`.**

### Step 7. Sign in

- **Demo practice:** click **Open the demo practice** on the landing page. The login (`demo@northbeam.example` / `northbeam-demo`) is filled in.
- **Your own workspace:** click **Start a workspace**. A new workspace gets the starter vocabulary, markets and bid rules, and nothing else: no archive, no gold set.
- **A second person:** in **Members**, create an invitation, then open the link in a private window and create an account with that email.

### Step 8. Stop

- In **Tab A**, press **Ctrl-C**. This stops the API, the web app and Studio.
- In **Tab B**, run `make stop`. The data is kept in the Docker volume `bidwright_pgdata`.

---

## Part 3: Run the pipeline step by step

Each step costs a little more than the one before. Check each result before moving on.

### Step 9. One RFP by hand (about 4 model calls, a few cents)

1. Sign in to the demo practice. The inbox lists 14 packs with status **Received**: the gold set.
2. Open `[gold P13]` and click **Extract** in the top-right block.
3. Wait about a minute, until the status reads *In review*.
4. Click any value on the right. Its source is circled in red on the PDF on the left.

Things to check on P13:

- GFA should read 110,000 m², converted from the Indian-grouped `11,84,030 sq ft`.
- The deadline should come from the email.
- *Preliminary Drawings* should map to Schematic Design.

Model calls and their cost are listed under **Model calls** (admins and owners).

### Step 10. Backtest one pack (about 4 model calls)

```bash
make eval-one CASE=P13
```

It prints a report: accuracy per field, conflicts found, and cost. The run is stored in the workspace and shown on the **Backtests** page. Then try a pack with a planted conflict:

```bash
make eval-one CASE=P02
```

P02's brief says about 42,180 m² and its area schedule says 38,000 m². The report should list it under "Conflicts flagged".

### Step 11. Backtest all 14 packs (about 56 model calls, a few dollars)

```bash
make eval
```

Read the summary at the top of the report: pricing-critical accuracy (target 90% or more), source highlights (target 100%), and planted conflicts flagged (target 4/4). Click any row on the **Backtests** page to open that RFP.

### Step 12. Re-score without spending (0 model calls)

After changing engine rules or scoring code:

```bash
make eval ARGS=--rescore
```

---

## Part 4: Keeping everything in sync

| When | Run in Tab B | Why |
|---|---|---|
| You pulled new code | `make setup` | Installs new dependencies, applies new migrations, reseeds. Safe to repeat |
| A migration was added | `make migrate`, then `cd prisma && pnpm run pull` | Database first, then Prisma reads the new shape |
| Reference data, the archive or gold packs changed | `make seed` | Reloads them into the demo workspace. Gold envelopes are recreated, so earlier extractions there are cleared |
| `.env` changed | Ctrl-C in Tab A, then `make dev` | The API reads `.env` at start-up |
| A database password changed in `.env` | `make db roles`, then restart `make dev` | Recreates the container with the new values and sets them on every login |
| You are not sure what state things are in | `make check`, `make status` | Preflight, plus workspaces and row counts |
| You want a clean slate | `make reset` | Deletes the database volume and runs `make setup` (asks first) |

### Looking at the database

- **Prisma Studio:** http://localhost:5555 while `make dev` is running. It sees every workspace.
- **psql:** `make psql` logs in as the owner, which row-level security still binds. To see a workspace's rows:
  ```sql
  select set_config('app.org_id', (select org_id::text from tenancy.organization where slug = 'northbeam'), false);
  ```
- **A GUI tool** (TablePlus, DBeaver): host `localhost`, port **5434**, database **bidwright**, user `bidwright_studio`. Its credential is the `BIDWRIGHT_STUDIO_PASSWORD` line in `.env`.

### Changing the database shape

1. Add a migration file in `service/migrations/versions/`. A new per-workspace table follows the pattern in `0001_baseline.py`: `org_id` with its default, `org_id` in the natural keys, and the row-level security policy.
2. `make migrate`
3. `cd prisma && pnpm run pull`
4. `make test` (includes the workspace isolation tests) and `make status`.

Never use `prisma migrate` or `prisma db push`. Alembic owns the shape.
