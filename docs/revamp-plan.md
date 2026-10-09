# Bidwright: from client demo to product

Status: 2026-10-09, in progress on branch `bidwright-revamp`.

The code began as a two-day proof of concept for one design firm. This plan turns it into Bidwright, a multi-tenant product that any architecture or design practice can sign up to and use with its own data.

## What Bidwright does

A practice receives an RFP pack: a main document, an area schedule, client terms, a cover email. Bidwright:

1. **Reads** every file in the pack and extracts what the client is asking for: typology, areas, services, stage package, location, deadlines and terms.
2. **Cites** each value to a highlighted line on a page, so a reviewer can check it in one click.
3. **Flags** where documents disagree, instead of picking one silently.
4. **Prices against the practice's own history:** comparable past proposals, fee benchmarks, location tier, billing currency, routing to a studio, and bid/no-bid rules.
5. **Backtests itself.** Each workspace keeps a gold set of packs with known answers. Every model, prompt or engine change is scored against it before anyone relies on it.

The reviewer confirms the record, and the confirmed version is locked. Every change has a reason and an author in an append-only log.

## Review findings

| Area | Finding | Decision |
|---|---|---|
| Narrative | The client's name appears in about 230 places: package name, database and roles, env vars, UI, prompts, reference rules, synthetic data, docs | Rename everything. The synthetic archive becomes the sample data for **Northbeam Studio**, a fictional practice that ships as the demo workspace |
| Narrative | `docs/poc-plan.md` and the Phase 1 `.docx` describe a client engagement | Remove from the product repo. Git history keeps them |
| Data model | No tenant boundary. Text primary keys (`proposal_id 'P01'`, `studio.code`, `fee_assumption.key`) collide as soon as a second firm loads its archive | Every firm-owned table gets `org_id`, included in natural keys |
| Data model | The vocabulary (typology, service, stage), countries, FX rates and schema definitions are market facts, not firm data | These stay global. Synonyms, locations and tiers, studios, rules, fee assumptions, benchmarks, archive, intake, lineage and evaluation become per-firm |
| Data model | Four migrations built for a demo, no deployed databases | Squash into a new baseline that includes tenancy. Local databases are rebuilt with `make reset` |
| Access | No authentication. `X-User` header with a hard-coded default user | Email and password sign-in, server-side sessions in an httpOnly cookie, roles per workspace |
| Isolation | Every query is unscoped | Postgres row-level security on every tenant table, keyed on `app.org_id` set per transaction. `org_id` defaults to that setting, so existing SQL keeps working. No setting means no rows |
| Engine | Prompt hard-codes the client's firm description | The prompt reads the workspace's practice profile |
| Frontend | One Vite page under the client's name, with no landing page | Multi-page Vite build: `/` is the landing page, `/app/` is the product |

## Tenancy and access

```
tenancy.organization   org_id uuid, slug, name, practice profile (disciplines, home markets)
tenancy.app_user       user_id uuid, email, name, password_hash (argon2)
tenancy.membership     (org_id, user_id), role
tenancy.session        token_hash, user_id, org_id, expires_at
```

Roles:

| Role | Can |
|---|---|
| `owner` | Everything, including deleting the workspace and changing other owners |
| `admin` | Members and roles below owner, reference data, rules, gold set, backtests |
| `estimator` | Upload RFP packs, edit fields, resolve conflicts, confirm records |
| `viewer` | Read everything in the workspace |

How isolation works:

- The API resolves the session, checks the member's role for the endpoint's permission, and sets a context variable for the workspace.
- A SQLAlchemy `begin` hook runs `set_config('app.org_id', …, true)` from that variable on every transaction, including background extraction.
- The API connects as `bidwright_app`, which is not the table owner, so policies always apply. Migrations and the seed run as `bidwright`.
- RLS policy: `org_id = NULLIF(current_setting('app.org_id', true), '')::uuid` for both `USING` and `WITH CHECK`.
- A new workspace gets the starter reference pack (vocabulary synonyms, markets, tiers, rules) copied in, and the firm edits it from there.

## Sequence

1. Brand: name, mark, colour and type tokens.
2. Rename: package, database, roles, env vars, prompts, reference rules, synthetic firm, docs.
3. Landing page.
4. Tenancy: squashed baseline with RLS, auth endpoints, permission checks, seed into a demo workspace.
5. UI: sign-in, workspace switcher, members page, cleaned-up inbox, review and admin screens.
6. Verify: unit tests, a cross-tenant isolation test, and the full flow in the browser.

## Not yet

- SSO or OIDC, email verification, password reset by email
- Billing and plans
- Per-firm vocabulary (custom typologies and services)
- Object storage per tenant (blobs are content-addressed on local disk; tenant access is enforced through `source_document`)
