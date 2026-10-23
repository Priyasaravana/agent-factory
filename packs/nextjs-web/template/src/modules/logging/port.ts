// The logging port: structured logs, one object per call. Log with
// `logger.info({ ... }, "message")`, never console.log.
export interface Logger {
  debug(fields: object, message?: string): void;
  info(fields: object, message?: string): void;
  warn(fields: object, message?: string): void;
  error(fields: object, message?: string): void;
}
