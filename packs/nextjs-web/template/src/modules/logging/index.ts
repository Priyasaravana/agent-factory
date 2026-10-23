import { moduleConfig } from "@/modules/config";

import { pinoLogger } from "./adapters/pino";
import { silentLogger } from "./adapters/silent";
import type { Logger } from "./port";

export type { Logger } from "./port";

/** The adapter `logging` names in src/modules/config.ts. */
export function createLogger(adapter = moduleConfig().logging): Logger {
  return adapter === "silent" ? silentLogger() : pinoLogger();
}

export const logger: Logger = createLogger();
