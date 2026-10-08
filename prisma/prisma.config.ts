// Prisma reads the bidwright database through the bidwright_studio role: rows yes, schema changes no.
// That role bypasses row-level security, so Studio shows every workspace. Development only.
// Alembic (service/migrations) owns the schema. Here we only run `db pull` (introspect),
// `generate` and `studio`; `migrate` / `db push` are refused by Postgres permissions.
import { defineConfig } from "prisma/config";
import { studioUrl } from "./studio-url";

export default defineConfig({
  schema: "schema.prisma",
  datasource: { url: studioUrl() },
});
