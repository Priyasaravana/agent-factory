import { Pool } from "pg";

import { SCHEMA } from "@/lib/schema";

// Postgres from DATABASE_URL (set by the Helm chart). Tests use an in-memory Postgres instead
// (tests/support/db.ts). Without either there is no database, and /api/readyz still reports ready.
type Queryable = Pick<Pool, "query">;
const g = globalThis as typeof globalThis & {
  appPool?: Queryable | null;
  appSchema?: Promise<void>;
};

export function pool(): Queryable | null {
  if (g.appPool === undefined) {
    const url = process.env.DATABASE_URL;
    g.appPool = url ? new Pool({ connectionString: url, max: 5 }) : null;
  }
  return g.appPool;
}

/** The pool with the tables created (once per process); throws when no database is configured. */
export async function db(): Promise<Queryable> {
  const p = pool();
  if (!p) throw new Error("no database: set DATABASE_URL");
  g.appSchema ??= p.query(SCHEMA).then(() => undefined);
  await g.appSchema;
  return p;
}

export async function dbReady(): Promise<boolean> {
  const p = pool();
  if (!p) return true;
  try {
    await p.query("select 1");
    return true;
  } catch {
    return false;
  }
}

/** For tests: use this database (or none) from now on. */
export function setDatabase(p: Queryable | null): void {
  g.appPool = p;
  delete g.appSchema;
}
