import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

// Types come straight from the engine's OpenAPI contract (npm run gen:api).
export type Order = components["schemas"]["Order"];
export type Run = components["schemas"]["Run"];
export type RunDetail = components["schemas"]["RunDetail"];
export type OrderDetail = components["schemas"]["OrderDetail"];
export type FactoryEvent = components["schemas"]["Event"];
export type StationView = components["schemas"]["StationView"];
export type Health = components["schemas"]["HealthView"];
export type ConfigView = components["schemas"]["ConfigView"];

export const api = createClient<paths>({ baseUrl: "" });

/** Unwrap an openapi-fetch result, throwing the API's `detail` message on error. */
export async function unwrap<T>(p: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const { data, error } = await p;
  if (error !== undefined) {
    const detail = (error as { detail?: unknown })?.detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail ?? error));
  }
  return data as T;
}

export const ACTIVE = new Set(["queued", "running"]);
