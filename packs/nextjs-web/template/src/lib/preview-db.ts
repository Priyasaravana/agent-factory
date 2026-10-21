import { setDatabase } from "@/lib/db";

// `make preview` only (PREVIEW_DATABASE=memory): an in-memory Postgres, so the factory can run the
// e2e suite against the app inside its sandbox before it builds an image (ADR-0043). pg-mem is a
// dev dependency: the import is left to Node at run time, so it is never bundled into the image.
export async function startPreviewDatabase(): Promise<void> {
  const name = "pg-mem";
  const { newDb } = (await import(
    /* webpackIgnore: true */ /* turbopackIgnore: true */ name
  )) as typeof import("pg-mem");
  const { Pool } = newDb().adapters.createPg();
  setDatabase(new Pool());
}
