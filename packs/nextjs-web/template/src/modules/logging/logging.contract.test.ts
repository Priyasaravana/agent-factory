import { Writable } from "node:stream";

import { describe, expect, it } from "vitest";

import { createLogger } from "@/modules/logging";

import { pinoLogger } from "./adapters/pino";
import { silentLogger } from "./adapters/silent";
import type { Logger } from "./port";

// The contract every logging adapter keeps: each level takes fields and a message and never throws.
const ADAPTERS: Record<string, () => Logger> = {
  pino: () => pinoLogger(sink([])),
  silent: silentLogger,
};

function sink(lines: string[]): Writable {
  return new Writable({
    write(chunk, _enc, done) {
      lines.push(String(chunk));
      done();
    },
  });
}

describe.each(Object.entries(ADAPTERS))("logging adapter %s", (_name, make) => {
  it("logs at every level without throwing", () => {
    const log = make();
    for (const level of ["debug", "info", "warn", "error"] as const) {
      expect(() => log[level]({ key: "value" }, "message")).not.toThrow();
      expect(() => log[level]({})).not.toThrow();
    }
  });
});

describe("pino adapter", () => {
  it("writes one JSON object per line with the service name", () => {
    const lines: string[] = [];
    pinoLogger(sink(lines)).info({ route: "/x" }, "hello");
    const entry = JSON.parse(lines[0] ?? "{}") as Record<string, unknown>;
    expect(entry).toMatchObject({ service: "app", route: "/x", msg: "hello" });
  });

  it("is the default; silent on request", () => {
    expect(createLogger("pino")).toHaveProperty("bindings");
    expect(createLogger("silent")).not.toHaveProperty("bindings");
  });
});
