import type { Logger } from "../port";

/** No logs at all (LOGGING=silent): for tests and tools that must not write to stdout. */
export function silentLogger(): Logger {
  const nothing = (): void => undefined;
  return { debug: nothing, info: nothing, warn: nothing, error: nothing };
}
