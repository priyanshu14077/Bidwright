// The bidwright_studio connection string, built from .env so no password lives in a tracked file.
import path from "node:path";
import { config } from "dotenv";

config({ path: path.resolve(import.meta.dirname, "../.env"), quiet: true });

export function studioUrl(): string {
  const password = process.env.BIDWRIGHT_STUDIO_PASSWORD;
  if (!password) throw new Error("BIDWRIGHT_STUDIO_PASSWORD is not set; run `make env` in the project root");
  return `postgresql://bidwright_studio:${encodeURIComponent(password)}@localhost:${process.env.POSTGRES_PORT ?? "5434"}/bidwright`;
}
