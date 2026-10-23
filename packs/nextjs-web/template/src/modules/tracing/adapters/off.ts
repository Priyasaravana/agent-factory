import type { Tracing } from "../port";

/** No tracing (no collector configured, or TRACING=off). */
export function noTracing(): Tracing {
  return { enabled: false, start: () => undefined };
}
