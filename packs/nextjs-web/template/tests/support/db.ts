import { newDb } from "pg-mem";

import { setDatabase } from "@/lib/db";

/** A fresh in-memory Postgres for one test; the app's `db()` creates the tables in it. */
export function memoryDatabase(): void {
  const { Pool } = newDb().adapters.createPg();
  setDatabase(new Pool());
}
