// Example: query the sog database through the generated Prisma client.
//   pnpm example
import path from "node:path";
import { config } from "dotenv";
import { PrismaPg } from "@prisma/adapter-pg";
import { PrismaClient } from "../generated/client.js";

config({ path: path.resolve(import.meta.dirname, "../../.env"), quiet: true });
const url = process.env.SOG_PRISMA_DATABASE_URL;
if (!url) throw new Error("SOG_PRISMA_DATABASE_URL is not set in .env");

const prisma = new PrismaClient({ adapter: new PrismaPg({ connectionString: url }) });

const proposals = await prisma.proposal.findMany({
  where: { dataset_version: "v0-synthetic" },
  select: { reference: true, title: true, typology: true, currency: true, fee_total_local: true, status: true,
            location: { select: { city: true, tier: true } } },
  orderBy: { reference: "asc" },
});
console.table(proposals.map((p) => ({ ...p, fee_total_local: Number(p.fee_total_local), location: `${p.location?.city} (${p.location?.tier})` })));

const inbox = await prisma.envelope.findMany({ select: { envelope_id: true, title: true, status: true }, take: 5 });
console.log(inbox);
await prisma.$disconnect();
