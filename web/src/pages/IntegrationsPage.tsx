import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, RefreshCw, ShieldCheck, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { api, unwrap, type CheckView, type PreflightView } from "../api/client";
import Problems from "../components/Problems";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { ago } from "../lib/time";
import { cn } from "../lib/utils";

const TONE: Record<string, string> = { ready: "ok", degraded: "warn", failed: "bad", unknown: "muted" };
const ICON: Record<string, ReactNode> = {
  ready: <CheckCircle2 className="text-ok" />,
  degraded: <CircleAlert className="text-warn" />,
  failed: <XCircle className="text-bad" />,
};

export default function IntegrationsPage() {
  const qc = useQueryClient();
  const preflight = useQuery({
    queryKey: ["preflight"],
    queryFn: () => unwrap(api.GET("/api/preflight")),
    refetchInterval: 30_000,
  });
  const delivery = useQuery({ queryKey: ["integrations"], queryFn: () => unwrap(api.GET("/api/integrations")) });
  const recheck = useMutation({
    mutationFn: () => unwrap(api.POST("/api/preflight")),
    onSuccess: (views) => {
      qc.setQueryData(["preflight"], views);
      qc.invalidateQueries({ queryKey: ["integrations"] });
      qc.invalidateQueries({ queryKey: ["health"] });
    },
  });

  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="m-0 text-2xl font-semibold tracking-tight">Readiness &amp; integrations</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Everything a run needs is checked before it starts. A failed check refuses new orders in seconds, before
            any model usage; degraded ones are shown but never block.
          </p>
        </div>
        <Button onClick={() => recheck.mutate()} disabled={recheck.isPending}>
          <RefreshCw className={cn(recheck.isPending && "animate-spin")} />
          {recheck.isPending ? "Checking…" : "Check now"}
        </Button>
      </div>
      <Problems error={recheck.error ?? preflight.error ?? null} />

      {preflight.isLoading && <div className="h-40 animate-pulse rounded-xl bg-muted" />}
      {preflight.data?.map((v) => <PreflightCard key={v.product_line} view={v} />)}

      {delivery.data && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Integrations</CardTitle>
              <CardDescription>
                External systems the delivery steps run through, configured in <code>.agent-factory/config.yaml</code>.
                Credentials are references, never values.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <table className="wide">
              <thead>
                <tr>
                  <td>integration</td>
                  <td>provider</td>
                  <td>capabilities</td>
                  <td>credential</td>
                  <td>used by</td>
                  <td>readiness</td>
                </tr>
              </thead>
              <tbody>
                {delivery.data.integrations.map((i) => (
                  <tr key={i.id} data-testid={`integration-${i.id}`}>
                    <td>
                      <strong>{i.id}</strong>
                      {Object.keys(i.settings ?? {}).length > 0 && (
                        <div className="small muted">
                          {Object.entries(i.settings ?? {})
                            .map(([k, val]) => `${k}: ${String(val)}`)
                            .join(" · ")}
                        </div>
                      )}
                    </td>
                    <td>{i.provider}</td>
                    <td>{i.capabilities.join(", ")}</td>
                    <td>{i.auth ? <code>{i.auth}</code> : <span className="muted">none</span>}</td>
                    <td>{(i.used_by ?? []).join(", ") || <span className="muted">—</span>}</td>
                    <td>
                      <span className={cn("pill", TONE[i.readiness.state] ?? "muted")}>{i.readiness.state}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {delivery.data && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Environments</CardTitle>
              <CardDescription>Which integration each delivery capability uses, per environment.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <table className="wide">
              <thead>
                <tr>
                  <td>environment</td>
                  <td>registry</td>
                  <td>scan</td>
                  <td>deploy</td>
                  <td>publish</td>
                  <td>product lines</td>
                </tr>
              </thead>
              <tbody>
                {delivery.data.environments.map((e) => (
                  <tr key={e.name}>
                    <td>
                      <strong>{e.name}</strong>
                    </td>
                    {["registry", "scan", "deploy", "publish"].map((c) => (
                      <td key={c}>
                        <code>{e.bindings[c]}</code>
                      </td>
                    ))}
                    <td>{(e.product_lines ?? []).join(", ") || <span className="muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function PreflightCard({ view }: { view: PreflightView }) {
  return (
    <Card data-testid={`preflight-${view.product_line}`}>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="size-4 text-primary" />
            {view.product_line}
            <span className={cn("pill", TONE[view.state] ?? "muted")}>{view.state}</span>
          </CardTitle>
          <CardDescription>
            environment <code>{view.environment}</code> · checked {ago(view.checked_at)} in {view.duration_ms} ms
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <ul className="m-0 grid list-none gap-1 p-0">
          {view.checks.map((c) => (
            <CheckRow key={c.id} check={c} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function CheckRow({ check: c }: { check: CheckView }) {
  const blocks = c.blocks ?? [];
  return (
    <li className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-2.5 rounded-lg px-2 py-2 hover:bg-muted/50 [&_svg]:size-4">
      <span className="mt-0.5">{ICON[c.state] ?? ICON.failed}</span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{c.title}</span>
          <span className="text-xs text-muted-foreground">{c.area}</span>
          {c.state === "failed" && blocks.length > 0 && (
            <span className="pill bad">blocks new {blocks.join(" and ")}s</span>
          )}
        </div>
        {(c.reasons ?? []).map((r) => (
          <div key={r} className="text-[13px] text-muted-foreground">
            {r}
          </div>
        ))}
      </div>
    </li>
  );
}
