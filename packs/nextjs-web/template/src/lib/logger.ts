import pino from "pino";

// Structured JSON logs on stdout, one object per line. Log with `logger.info({ ... }, "message")`,
// never console.log.
export const logger = pino({
  level: process.env.LOG_LEVEL ?? "info",
  base: { service: process.env.OTEL_SERVICE_NAME ?? "app" },
  timestamp: pino.stdTimeFunctions.isoTime,
});
