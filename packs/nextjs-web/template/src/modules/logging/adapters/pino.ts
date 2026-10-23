import pino from "pino";

import type { Logger } from "../port";

/** Structured JSON logs on stdout, one object per line. */
export function pinoLogger(
  destination?: pino.DestinationStream,
): Logger & { bindings(): Record<string, unknown> } {
  const options: pino.LoggerOptions = {
    level: process.env.LOG_LEVEL ?? "info",
    base: { service: process.env.OTEL_SERVICE_NAME ?? "app" },
    timestamp: pino.stdTimeFunctions.isoTime,
  };
  return destination ? pino(options, destination) : pino(options);
}
