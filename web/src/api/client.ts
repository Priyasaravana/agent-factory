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
export type WorkflowView = components["schemas"]["WorkflowView"];
export type AgentView = components["schemas"]["AgentView"];
export type WorkflowVersionInfo = components["schemas"]["WorkflowVersionInfo"];
export type WorkflowSummary = components["schemas"]["WorkflowSummary"];
export type TemplateInfo = components["schemas"]["TemplateInfo"];
export type AgentSpec = components["schemas"]["AgentSpec"];
export type RefDoc = components["schemas"]["RefDoc"];
export type DraftView = components["schemas"]["DraftView"];
export type CatalogView = components["schemas"]["CatalogView"];
export type SkillInfo = components["schemas"]["SkillInfo"];
export type SkillPreview = components["schemas"]["SkillPreview"];

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
