// The tracing port: start distributed tracing for this process, once, at server start
// (src/instrumentation.ts).
export interface Tracing {
  readonly enabled: boolean;
  start(serviceName: string): void;
}
