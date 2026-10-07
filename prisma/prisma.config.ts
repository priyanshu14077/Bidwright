// Prisma reads the sog database through the sog_prisma role: rows yes, schema changes no.
// Alembic (service/migrations) owns the schema. Here we only run `db pull` (introspect),
// `generate` and `studio`; `migrate` / `db push` are refused by Postgres permissions.
import path from "node:path";
import { config } from "dotenv";
import { defineConfig, env } from "prisma/config";

config({ path: path.resolve(import.meta.dirname, "../.env"), quiet: true });

export default defineConfig({
  schema: "schema.prisma",
  datasource: { url: env("SOG_PRISMA_DATABASE_URL") },
});
