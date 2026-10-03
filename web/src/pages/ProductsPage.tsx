import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Inbox, Plus, Rocket, Sparkles } from "lucide-react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ACTIVE, api, unwrap } from "../api/client";
import Problems from "../components/Problems";
import StatusPill from "../components/StatusPill";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { cn } from "../lib/utils";
import { ago } from "../lib/time";

export default function ProductsPage() {
  const [showArchived, setShowArchived] = useState(false);
  const products = useQuery({
    queryKey: ["products", showArchived],
    queryFn: () => unwrap(api.GET("/api/products", { params: { query: { include_archived: showArchived } } })),
    refetchInterval: 5_000,
  });
  const [params, setParams] = useSearchParams();
  const titleRef = useRef<HTMLInputElement>(null);
  const focusNew = () => {
    titleRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    titleRef.current?.focus({ preventScroll: true });
  };
  useEffect(() => {
    if (params.get("new")) {
      focusNew();
      params.delete("new");
      setParams(params, { replace: true });
    }
  }, [params, setParams]);

  const list = products.data ?? [];
  const live = list.filter((o) => !o.archived_at);
  const count = (pred: (s: string) => boolean) => live.filter((o) => o.latest_status && pred(o.latest_status)).length;

  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="m-0 text-2xl font-semibold tracking-tight">Products</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Requirements in, verified running software out. You give feedback; agents do the rest.
          </p>
        </div>
        <Button onClick={focusNew}>
          <Plus /> New product
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Products" value={live.length} />
        <Stat label="In progress" value={count((s) => ACTIVE.has(s))} tone="info" />
        <Stat label="Awaiting feedback" value={count((s) => s === "awaiting_feedback")} tone="ok" />
        <Stat
          label="Need attention"
          value={count((s) =>
            ["held", "failed", "needs_input", "interrupted", "paused_limits", "awaiting_approval", "awaiting_risk_approval"].includes(s),
          )}
          tone="bad"
        />
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,1fr)_400px]">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Recent products</CardTitle>
              <CardDescription>Newest first. Open one to follow its change live.</CardDescription>
            </div>
            <label className="check text-xs text-muted-foreground">
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
              show archived
            </label>
          </CardHeader>
          <CardContent className="px-2 pb-2">
            {products.isLoading && <Skeleton />}
            {products.error && <p className="error px-3">{products.error.message}</p>}
            {products.data?.length === 0 && (
              <div className="grid place-items-center gap-2 px-3 py-12 text-center text-muted-foreground">
                <Inbox className="size-8 opacity-60" />
                <p className="m-0 text-sm">No products yet. Describe your first app on the right.</p>
              </div>
            )}
            <ul className="m-0 list-none p-0">
              {list.map((o) => (
                <li key={o.id} className="group flex items-center gap-2 rounded-lg pr-3 transition hover:bg-muted/70">
                  <Link
                    to={`/products/${o.id}`}
                    className="flex min-w-0 flex-1 flex-wrap items-center gap-3 px-3 py-3 text-foreground no-underline hover:no-underline"
                  >
                    <span aria-hidden className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-sm font-semibold uppercase text-primary">
                      {o.title.slice(0, 1)}
                    </span>
                    <span className="min-w-0 flex-1 basis-40">
                      <span className="block truncate font-medium">{o.title}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {o.slug} · {o.blueprint} · {ago(o.created_at)}
                        {o.created_by ? ` · by ${o.created_by}` : ""}
                      </span>
                    </span>
                    {o.archived_at ? (
                      <span className="pill muted">archived</span>
                    ) : (
                      o.latest_status && <StatusPill status={o.latest_status} />
                    )}
                  </Link>
                  {o.app_url && (
                    <a href={o.app_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-xs">
                      app <ArrowUpRight className="size-3" />
                    </a>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <NewOrder titleRef={titleRef} />
      </div>
    </div>
  );
}

function NewOrder({ titleRef }: { titleRef: RefObject<HTMLInputElement> }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const config = useQuery({ queryKey: ["config"], queryFn: () => unwrap(api.GET("/api/config")) });
  const [title, setTitle] = useState("");
  const [requirements, setRequirements] = useState("");
  const [line, setLine] = useState("fastapi-service");
  const [format, setFormat] = useState<"prose" | "spec">("prose");
  const loadSpec = async (file: File | undefined) => {
    if (!file) return;
    setRequirements(await file.text());
    setFormat("spec");
    if (!title) setTitle(file.name.replace(/\.(md|markdown|txt)$/i, "").replace(/[-_]+/g, " "));
  };
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/products", { body: { title, requirements, blueprint: line, requirements_format: format } }),
      ),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["products"] });
      toast.success("Product started", { description: d.product.title });
      nav(`/products/${d.product.id}`);
    },
  });
  return (
    <Card className="relative overflow-hidden lg:sticky lg:top-20">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-primary via-fuchsia-500 to-info" />
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="size-4 text-primary" /> New product
          </CardTitle>
          <CardDescription>
            Describe what you want. The factory specifies, designs, builds, tests, deploys and verifies it, then asks for
            your feedback.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <label>
            Title
            <input
              ref={titleRef}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Bookmarks service"
              required
              minLength={3}
            />
          </label>
          <label>
            Blueprint
            <select value={line} onChange={(e) => setLine(e.target.value)}>
              {Object.entries(config.data?.blueprints ?? { "fastapi-service": "" }).map(([k, v]) => (
                <option key={k} value={k}>
                  {k} {v ? `— ${v}` : ""}
                </option>
              ))}
            </select>
          </label>
          <div className="grid gap-1.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-medium" id="req-label">
                {format === "spec" ? "Specification" : "Requirements"}
              </span>
              <div className="flex gap-1 rounded-lg border bg-card p-0.5" role="radiogroup" aria-label="How you describe it">
                {(
                  [
                    ["prose", "Describe it"],
                    ["spec", "I have a spec"],
                  ] as const
                ).map(([v, label]) => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={format === v}
                    className={`tab ${format === v ? "active" : ""}`}
                    onClick={() => setFormat(v)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <textarea
              aria-labelledby="req-label"
              rows={format === "spec" ? 14 : 9}
              value={requirements}
              onChange={(e) => setRequirements(e.target.value)}
              placeholder={
                format === "spec"
                  ? "Paste your specification (Markdown). Its wording and numbering are kept; intake only fills gaps."
                  : "A REST service to save bookmarks and notes with tags. Filter by tag, search notes…"
              }
              required
              minLength={10}
              className="font-sans text-sm"
            />
            {format === "spec" && (
              <label className="text-xs font-normal text-muted-foreground">
                or load a Markdown file
                <input
                  type="file"
                  accept=".md,.markdown,.txt,text/markdown,text/plain"
                  onChange={(e) => void loadSpec(e.target.files?.[0])}
                  className="text-xs"
                />
              </label>
            )}
          </div>
          <Button disabled={create.isPending} size="lg">
            <Rocket /> {create.isPending ? "Submitting…" : "Create product"}
          </Button>
          <Problems error={create.error} />
        </form>
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "ok" | "info" | "bad" }) {
  if (!value) tone = undefined; // zero is not news
  return (
    <div className="rounded-xl border bg-card px-4 py-3 shadow-sm">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div
        className={cn(
          "mt-1 text-2xl font-semibold tabular-nums",
          tone === "ok" && "text-ok",
          tone === "info" && "text-info",
          tone === "bad" && "text-bad",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="grid gap-2 p-3">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-12 animate-pulse rounded-lg bg-muted" />
      ))}
    </div>
  );
}
