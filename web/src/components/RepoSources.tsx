import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Lock, Pencil, Plus, Trash2, Webhook } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import type { components } from "../api/schema";
import { useIsAdmin } from "../auth";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { ConfirmDialog } from "./ui/confirm-dialog";

type Source = components["schemas"]["RepoSourceView"];
type SourceInput = components["schemas"]["RepoSourceInput"];

export const sourcesQuery = { queryKey: ["repo-sources"], queryFn: () => unwrap(api.GET("/api/repo-sources")) };

/** The sources covering a repo URL, best first: those naming its owner, then those covering any owner
 * (the same rule as the engine's `repo_sources.candidates`; the engine decides, this only previews). */
export function coveringSources(sources: Source[], url: string): Source[] {
  const m = /^https:\/\/([^/\s]+)\/([^/\s]+)\/[^/\s]+?(\.git)?\/?$/.exec(url.trim());
  if (!m) return [];
  const host = m[1].toLowerCase();
  const owner = m[2].toLowerCase();
  const onHost = sources.filter((s) => s.host === host);
  const named = onHost.filter((s) => s.owners.some((o) => o.toLowerCase() === owner));
  const wild = onHost.filter((s) => s.owners.includes("*") && !named.includes(s));
  return [...named, ...wild];
}

const AUDIT_VERB: Record<string, string> = {
  created: "added",
  updated: "changed",
  deleted: "deleted",
  product_moved: "moved a repo to",
};

function scope(s: Source): string {
  return s.owners.includes("*") ? "any owner" : s.owners.join(", ");
}

function Ready({ ready, label }: { ready: boolean | null | undefined; label: string }) {
  if (ready == null) return null;
  return (
    <span className={cn("pill", ready ? "ok" : "warn")} title={ready ? `${label} resolves` : `${label} doesn't resolve`}>
      {ready ? "resolves" : "not set"}
    </span>
  );
}

/** Onboarding: which source will reach this repo, with a choice when more than one covers it. */
export function SourcePicker({
  url,
  value,
  onChange,
}: {
  url: string;
  value: string | null;
  onChange: (id: string | null) => void;
}) {
  const sources = useQuery(sourcesQuery);
  const all = sources.data?.sources ?? [];
  const found = coveringSources(all, url);
  if (!url.trim() || !sources.data) return null;
  if (found.length === 0) {
    return (
      <p className="m-0 rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground" data-testid="source-none">
        No repository source covers this host and owner yet. An admin adds one on{" "}
        <a href="/integrations">Integrations → Repository sources</a>.
      </p>
    );
  }
  const picked = found.find((s) => s.id === value) ?? found[0];
  return (
    <label data-testid="source-picker">
      Reached through
      {found.length === 1 ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-sm font-normal">
          <KeyRound className="size-4 text-primary" />
          <strong>{picked.title}</strong>
          <span className="text-muted-foreground">
            {picked.host} · {scope(picked)}
          </span>
          {picked.access === "read" && <span className="pill muted">read only: assessments, no pull requests</span>}
        </div>
      ) : (
        <select value={picked.id} onChange={(e) => onChange(e.target.value)}>
          {found.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title} ({s.host} · {scope(s)}
              {s.access === "read" ? " · read only" : ""})
            </option>
          ))}
        </select>
      )}
    </label>
  );
}

const EMPTY: SourceInput = {
  id: "",
  title: "",
  host: "github.com",
  api_url: "",
  owners: [],
  access: "write",
  token_ref: "",
  webhook_secret_ref: "",
};

function SourceForm({
  initial,
  editing,
  tokenRefs,
  allowedHosts,
  onDone,
}: {
  initial: SourceInput;
  editing: boolean;
  tokenRefs: string[];
  allowedHosts: string[];
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [v, setV] = useState<SourceInput>(initial);
  const [owners, setOwners] = useState(initial.owners.join(", "));
  const set = (k: keyof SourceInput) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  const save = useMutation({
    mutationFn: () => {
      const body: SourceInput = {
        ...v,
        owners: owners
          .split(/[\s,]+/)
          .map((o) => o.trim())
          .filter(Boolean),
        token_ref: v.token_ref?.trim() || null,
        webhook_secret_ref: v.webhook_secret_ref?.trim() || null,
      };
      return editing
        ? unwrap(api.PUT("/api/repo-sources/{source_id}", { params: { path: { source_id: v.id } }, body }))
        : unwrap(api.POST("/api/repo-sources", { body }));
    },
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: ["repo-sources"] });
      toast.success(editing ? "Source updated" : "Source added", { description: s.title });
      onDone();
    },
  });
  const example = (tokenRefs[0] ?? "env://REPO_*").replace("*", "ACME_TOKEN");
  return (
    <form
      className="grid gap-3 rounded-xl border bg-muted/30 p-4 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
      data-testid="source-form"
    >
      <label>
        Id
        <input value={v.id} onChange={set("id")} placeholder="acme-github" required disabled={editing} pattern="[a-z0-9][a-z0-9-]{1,39}" />
      </label>
      <label>
        Title
        <input value={v.title ?? ""} onChange={set("title")} placeholder="Acme on GitHub" />
      </label>
      <label>
        Host
        <select value={v.host ?? "github.com"} onChange={set("host")}>
          {allowedHosts.map((h) => (
            <option key={h} value={h}>
              {h}
            </option>
          ))}
        </select>
      </label>
      <label>
        Orgs or users it covers
        <input value={owners} onChange={(e) => setOwners(e.target.value)} placeholder="acme, acme-labs" required />
      </label>
      <label>
        Access
        <select value={v.access ?? "write"} onChange={set("access")}>
          <option value="write">Write: assess and open pull requests</option>
          <option value="read">Read: assess only, never push</option>
        </select>
      </label>
      <label>
        API <span className="font-normal text-muted-foreground">(optional: the host's default)</span>
        <input value={v.api_url ?? ""} onChange={set("api_url")} placeholder="https://ghe.example.com/api/v3" />
      </label>
      <label>
        Token reference
        <input value={v.token_ref ?? ""} onChange={set("token_ref")} placeholder={example} />
      </label>
      <label>
        Webhook secret reference <span className="font-normal text-muted-foreground">(optional)</span>
        <input value={v.webhook_secret_ref ?? ""} onChange={set("webhook_secret_ref")} placeholder={example.replace("TOKEN", "HOOK")} />
      </label>
      <p className="m-0 text-xs text-muted-foreground sm:col-span-2">
        A reference names where the secret lives; the value never goes in here. Allowed: {" "}
        {tokenRefs.map((r) => (
          <code key={r} className="mr-1">
            {r}
          </code>
        ))}
        (set by the factory owner in <code>existing_repos.source_token_refs</code>). Use a fine-grained token per org:
        Contents and Pull requests read/write, Checks and Commit statuses read (write: statuses).
      </p>
      <div className="flex gap-2 sm:col-span-2">
        <Button disabled={save.isPending}>{save.isPending ? "Saving…" : editing ? "Save" : "Add source"}</Button>
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
      </div>
      <div className="sm:col-span-2">
        <Problems error={save.error} />
      </div>
    </form>
  );
}

/** Integrations: the repository sources, editable by admins (ADR-0047). */
export function RepoSourcesCard() {
  const qc = useQueryClient();
  const admin = useIsAdmin();
  const q = useQuery(sourcesQuery);
  const [form, setForm] = useState<{ initial: SourceInput; editing: boolean } | null>(null);
  const [deleting, setDeleting] = useState<Source | null>(null);
  const del = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/repo-sources/{source_id}", { params: { path: { source_id: id } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["repo-sources"] });
      toast.success("Source deleted");
      setDeleting(null);
    },
  });
  const data = q.data;
  const editable = admin && (data?.editable ?? false);
  return (
    <Card data-testid="repo-sources">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <KeyRound className="size-4 text-primary" /> Repository sources
            </CardTitle>
            <CardDescription>
              How the factory reaches team repos: each source covers orgs on one host with its own token. A repo uses the
              source that names its owner; it never falls back to another token.
            </CardDescription>
          </div>
          {editable && !form && (
            <Button size="sm" onClick={() => setForm({ initial: EMPTY, editing: false })}>
              <Plus /> Add source
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="grid gap-4">
        {form && data && (
          <SourceForm
            initial={form.initial}
            editing={form.editing}
            tokenRefs={data.token_refs}
            allowedHosts={data.allowed_hosts}
            onDone={() => setForm(null)}
          />
        )}
        <Problems error={q.error ?? del.error ?? null} />
        {q.isLoading && <div className="h-24 animate-pulse rounded-xl bg-muted" />}
        {data && (
          <ul className="m-0 grid list-none divide-y p-0" aria-label="Repository sources">
            <li className="hidden grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_5rem_minmax(0,1.8fr)_3rem_5rem] gap-3 pb-2 text-xs uppercase tracking-wide text-muted-foreground md:grid">
              <span>source</span>
              <span>covers</span>
              <span>access</span>
              <span>{admin ? "credentials" : ""}</span>
              <span>repos</span>
              <span />
            </li>
            {data.sources.map((s) => (
              <li
                key={s.id}
                data-testid={`source-${s.id}`}
                className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-1.5 py-3 text-sm md:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_5rem_minmax(0,1.8fr)_3rem_5rem] md:items-center"
              >
                <div className="min-w-0">
                  <strong>{s.title}</strong>
                  <div className="text-xs text-muted-foreground">
                    {s.id} · {s.origin === "admin" ? `added by ${s.created_by ?? "an admin"}` : "from the config file"}
                  </div>
                </div>
                <div className="col-start-2 row-start-1 flex justify-end gap-1 md:col-start-6 md:row-start-auto">
                  {editable && s.origin === "admin" && (
                    <>
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Edit ${s.title}`}
                        onClick={() =>
                          setForm({
                            editing: true,
                            initial: {
                              id: s.id,
                              title: s.title,
                              host: s.host,
                              api_url: s.api_url ?? "",
                              owners: s.owners,
                              access: s.access as SourceInput["access"],
                              token_ref: s.token_ref ?? "",
                              webhook_secret_ref: s.webhook_secret_ref ?? "",
                            },
                          })
                        }
                      >
                        <Pencil />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Delete ${s.title}`}
                        disabled={(s.products ?? 0) > 0}
                        title={(s.products ?? 0) > 0 ? "In use: archive or move its repos first" : undefined}
                        onClick={() => setDeleting(s)}
                      >
                        <Trash2 />
                      </Button>
                    </>
                  )}
                </div>
                <div className="col-span-2 min-w-0 md:col-span-1 md:col-start-2 md:row-start-1">
                  {s.host}
                  <span className="text-muted-foreground"> · {scope(s)}</span>
                </div>
                <div className="col-span-2 flex flex-wrap items-center gap-2 md:col-span-1 md:col-start-3 md:row-start-1">
                  <span className={cn("pill", s.access === "write" ? "info" : "muted")}>
                    {s.access === "read" && <Lock />}
                    {s.access}
                  </span>
                  <span className="text-xs text-muted-foreground md:hidden">
                    {s.products ?? 0} repo{s.products === 1 ? "" : "s"}
                  </span>
                </div>
                <div className="col-span-2 grid min-w-0 gap-1 md:col-span-1 md:col-start-4 md:row-start-1">
                  {admin && (
                    <>
                      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                        <KeyRound className="size-3.5 shrink-0 text-muted-foreground" />
                        {s.token_ref ? (
                          <code className="break-all">{s.token_ref}</code>
                        ) : (
                          <span className="text-muted-foreground">no token: public repos only</span>
                        )}
                        <Ready ready={s.token_ready} label="token" />
                      </div>
                      {s.webhook_secret_ref && (
                        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                          <Webhook className="size-3.5 shrink-0 text-muted-foreground" />
                          <code className="break-all">{s.webhook_secret_ref}</code>
                          <Ready ready={s.webhook_ready} label="webhook secret" />
                        </div>
                      )}
                    </>
                  )}
                </div>
                <div className="hidden md:col-start-5 md:row-start-1 md:block">{s.products ?? 0}</div>
              </li>
            ))}
          </ul>
        )}
        {admin && (data?.audit?.length ?? 0) > 0 && (
          <details className="rounded-lg border px-3 py-2 text-sm" data-testid="source-audit">
            <summary className="cursor-pointer font-medium">Recent changes ({data?.audit?.length})</summary>
            <ul className="m-0 mt-2 grid list-none gap-1.5 p-0 text-xs">
              {(data?.audit ?? []).map((a, i) => (
                <li key={i} className="flex flex-wrap gap-x-2">
                  <span className="text-muted-foreground">{new Date(String(a.at)).toLocaleString()}</span>
                  <strong>{String(a.by)}</strong>
                  <span>{AUDIT_VERB[String(a.action)] ?? String(a.action)}</span>
                  <code>{String(a.source_id)}</code>
                  {Array.isArray(a.fields) && a.fields.length > 0 && (
                    <span className="text-muted-foreground">({(a.fields as string[]).join(", ")})</span>
                  )}
                  {a.detail ? <span className="text-muted-foreground">{String(a.detail)}</span> : null}
                </li>
              ))}
            </ul>
          </details>
        )}
        <ConfirmDialog
          open={deleting !== null}
          onOpenChange={(o) => !o && setDeleting(null)}
          title={`Delete ${deleting?.title ?? "source"}?`}
          confirm="Delete source"
          destructive
          busy={del.isPending}
          onConfirm={() => deleting && del.mutate(deleting.id)}
        >
          <p className="m-0">
            Repos on {deleting?.host} owned by {deleting ? scope(deleting) : ""} can no longer be onboarded through it.
            The secret itself is not touched.
          </p>
        </ConfirmDialog>
      </CardContent>
    </Card>
  );
}

/** A team repo's source on its product page; an admin can move it to another that covers the repo. */
export function ProductSource({ productId, repoUrl, current }: { productId: string; repoUrl: string; current?: string | null }) {
  const qc = useQueryClient();
  const admin = useIsAdmin();
  const q = useQuery(sourcesQuery);
  const move = useMutation({
    mutationFn: (source: string) =>
      unwrap(api.POST("/api/products/{product_id}/repo-source", { params: { path: { product_id: productId } }, body: { source } })),
    onSuccess: (d) => {
      qc.setQueryData(["product", productId], d);
      qc.invalidateQueries({ queryKey: ["product", productId] });
      qc.invalidateQueries({ queryKey: ["repo-sources"] });
      toast.success("Source changed", { description: "The next clone, push and pull request use it" });
    },
  });
  const all = q.data?.sources ?? [];
  const found = coveringSources(all, repoUrl);
  const src = all.find((s) => s.id === current);
  if (admin && found.length > 1) {
    return (
      <span className="grid gap-1">
        <select
          aria-label="Repository source"
          value={current ?? ""}
          disabled={move.isPending}
          onChange={(e) => move.mutate(e.target.value)}
          className="text-sm"
        >
          {!current && <option value="">chosen at the next change</option>}
          {found.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title} ({s.access})
            </option>
          ))}
        </select>
        <Problems error={move.error} />
      </span>
    );
  }
  if (!current) return <span className="text-muted-foreground">chosen at the next change</span>;
  return (
    <span>
      {src?.title ?? current}
      {src?.access === "read" && <span className="pill muted ml-1.5">read only</span>}
    </span>
  );
}
