import { afterEach, describe, expect, it, vi } from "vitest";

import { GET as healthz } from "@/app/api/healthz/route";
import { GET as metrics } from "@/app/api/metrics/route";
import { GET as readyz } from "@/app/api/readyz/route";
import { setDatabase } from "@/lib/db";

import { memoryDatabase } from "./support/db";

describe("operational endpoints", () => {
  afterEach(() => setDatabase(null));

  it("answers liveness", async () => {
    const res = healthz();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ok" });
  });

  it("is ready without a database", async () => {
    const res = await readyz();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ready" });
  });

  it("is not ready when the database does not answer", async () => {
    const query = vi.fn().mockRejectedValue(new Error("down"));
    setDatabase({ query } as never);
    const res = await readyz();
    expect(res.status).toBe(503);
    expect(query).toHaveBeenCalledWith("select 1");
  });

  it("is ready when the database answers", async () => {
    memoryDatabase();
    expect((await readyz()).status).toBe(200);
  });

  it("exposes Prometheus metrics, counting requests", async () => {
    healthz();
    const res = await metrics();
    expect(res.headers.get("content-type")).toContain("text/plain");
    const body = await res.text();
    expect(body).toContain('http_requests_total{route="/api/healthz",status="200"}');
    expect(body).toContain("process_cpu_user_seconds_total");
  });
});
