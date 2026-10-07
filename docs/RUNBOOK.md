# Runbook: running the SOG proof of concept

Every command below runs from the **project root**:

```bash
cd ~/Documents/fruition-projects/sog
```

The `Makefile` lives there, and each `make` command moves into the right folder by itself. You never need to `cd` into `service/`, `frontend/` or `prisma/`.

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

1. Open `.env` in the project root.
2. On the line `ANTHROPIC_API_KEY=`, paste the key directly after the `=`. Leave no spaces and no quotes:
   ```
   ANTHROPIC_API_KEY=sk-ant-api03-...
   ```
3. **Save the file (Cmd+S).** The services read `.env` from disk, so an unsaved key does not count.

### Step 3. Build the database and install everything

In **Tab B**:

```bash
make setup
```

This runs in order:

1. starts the Postgres container `sog-postgres-1`;
2. creates the logins;
3. installs the Python, frontend and Prisma dependencies;
4. applies the migrations;
5. seeds the data;
6. prints a table with every table's row count.

It takes about a minute. The last line of the table should read `ref | typology_cost_ratio | 6`, and `archive | proposal` should show `14`.

### Step 4. Check everything

In **Tab B**:

```bash
make check
```

Every line under Database, Model and Tools must say `OK`. If `API key` says FAIL, go back to Step 2 and save the file.

---

## Part 2: Every working session

### Step 5. Start the database (if Docker was restarted)

In **Tab B**:

```bash
make db
```

This does nothing if the database is already running.

### Step 6. Start the services

In **Tab A**:

```bash
make dev
```

Wait for the three addresses to print, then open them:

| Address | What it is |
|---|---|
| http://localhost:5173 | Review UI: inbox, review, accuracy, reference data, model calls |
| http://localhost:8000/docs | API (try any endpoint from the browser) |
| http://localhost:5555 | Prisma Studio: browse every table |

**Restart `make dev` (Ctrl-C, then `make dev` again) whenever you change `.env`.** The API reads it only at start-up.

### Step 7. Stop

- In **Tab A**, press **Ctrl-C**. This stops the API, UI and Studio.
- In **Tab B**, run `make stop`. This stops the database container. The data is kept in the Docker volume `sog_pgdata`.

---

## Part 3: Run the pipeline step by step

Each step costs a little more than the one before. Do them in order and check each result before moving on.

### Step 8. One RFP by hand in the UI (about 4 model calls, a few cents)

1. Open http://localhost:5173. The inbox lists 14 RFPs with status **Received**. These are the gold packs.
2. Open **Arabian Sea Towers** (`[gold P13]`), and click **Extract** in the top-right block.
3. Wait about a minute. The status changes to *In review*.
4. Click any value on the right. The source is circled in red on the PDF on the left.

Things to check on P13:

- GFA should read 110,000 m², converted from the Indian `11,84,030 sq ft`.
- The deadline should come from the email.
- *Preliminary Drawings* should map to Schematic Design.

Model calls and their cost are listed under **Model calls** in the top menu.

### Step 9. Score one pack (about 4 model calls)

In **Tab B**:

```bash
make eval-one CASE=P13
```

It prints a report: accuracy per field, conflicts found, and cost. The run is also stored in the database (`eval.run`) and shown on the **Accuracy** page.

Then try a pack with a planted conflict:

```bash
make eval-one CASE=P02
```

P02's brief says ~42,180 m² and its area schedule says 38,000 m². The report should list it under "Conflicts flagged".

### Step 10. Score all 14 packs (about 56 model calls, a few dollars)

```bash
make eval
```

This takes several minutes. Read the summary table at the top of the report: pricing-critical accuracy (target ≥ 90%), source highlights (target 100%), and planted conflicts flagged (target 4/4). Click any row on the **Accuracy** page to open that RFP and see what went wrong.

### Step 11. Re-score without spending (0 model calls)

After changing engine rules or scoring code:

```bash
make eval ARGS=--rescore
```

or directly: `cd service && uv run python -m sog.evaluation --rescore`.

---

## Part 4: Keeping everything in sync

| When | Run in Tab B | Why |
|---|---|---|
| You pulled new code or someone added a migration | `make setup` | Installs new dependencies, applies new migrations, reseeds. Safe to repeat |
| A migration was added and nothing else changed | `make migrate` then `cd prisma && pnpm pull && cd ..` | Database first, then Prisma reads the new shape |
| Reference data, the archive JSON or the gold packs changed | `make seed` | Reloads them. Gold envelopes are recreated, so earlier extractions of them are cleared |
| `.env` changed | Ctrl-C in Tab A, then `make dev` | The API reads `.env` at start-up |
| You are not sure what state things are in | `make check` and `make status` | Preflight, plus row counts for every table |
| You want a clean slate | `make reset` | Deletes the database volume and runs `make setup` (asks first) |

### Looking at the database

- **Prisma Studio:** http://localhost:5555, while `make dev` is running.
- **psql:** `make psql`. Then `\dn` lists the schemas, `\dt archive.*` lists tables, and `\q` quits.
- **A GUI tool** (TablePlus, DBeaver): host `localhost`, port **5434**, database **sog**, user `sog_prisma`, password `sog_prisma`. The tables are in the schemas `ref`, `archive`, `lineage`, `intake` and `eval`, not in `public`.

### Changing the database shape

1. Add a migration file in `service/migrations/versions/` (copy the pattern of `0004_dataset_eval.py`).
2. `make migrate`
3. `cd prisma && pnpm pull && cd ..`
4. `make status` to confirm.

Never use `prisma migrate` or `prisma db push`. Alembic owns the shape, and the Prisma login cannot change it.
