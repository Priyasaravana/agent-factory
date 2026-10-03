import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

// Types come straight from the engine's OpenAPI contract (npm run gen:api).
export type Product = components["schemas"]["Product"];
export type Change = components["schemas"]["Change"];
export type ChangeDetail = components["schemas"]["ChangeDetail"];
export type ProductDetail = components["schemas"]["ProductDetail"];
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
export type PreflightView = components["schemas"]["PreflightView"];
export type CheckView = components["schemas"]["CheckView"];

/** An API error; `problems` lists what a preflight or validation found. */
export class ApiError extends Error {
  constructor(message: string, readonly problems: string[] = [], readonly status = 0) {
    super(message);
  }
}

export const api = createClient<paths>({ baseUrl: "" });

/** Unwrap an openapi-fetch result, throwing the API's `detail` message on error. */
export async function unwrap<T>(p: Promise<{ data?: T; error?: unknown; response?: Response }>): Promise<T> {
  const { data, error, response } = await p;
  if (response && (response.status === 401 || response.status === 403) && error !== undefined) {
    // the session expired or the password must change: let the auth shell re-check
    window.dispatchEvent(new Event("factory-auth-changed"));
  }
  if (error !== undefined) {
    const { detail, problems } = (error ?? {}) as { detail?: unknown; problems?: unknown };
    throw new ApiError(
      typeof detail === "string" ? detail : JSON.stringify(detail ?? error),
      Array.isArray(problems) ? problems.map(String) : [],
      response?.status ?? 0,
    );
  }
  return data as T;
}

export const ACTIVE = new Set(["queued", "running"]);
