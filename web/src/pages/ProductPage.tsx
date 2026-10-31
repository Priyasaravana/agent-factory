import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowLeft,
  ArrowUpRight,
  FileText,
  GitBranch,
  Globe,
  Layers,
} from "lucide-react";
import { toast } from "sonner";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ACTIVE, KIND_LABEL, api, unwrap } from "../api/client";
import Problems from "../components/Problems";
import ChangePanel from "../components/ChangePanel";
import WorkLedger from "../components/WorkLedger";
import NeedsYou, { useAttention } from "../components/NeedsYou";
import NextIteration from "../components/NextIteration";
import { Panel } from "../components/ui/panel";
import SpendCard from "../components/SpendCard";
import { Button } from "../components/ui/button";
import { ConfirmDialog } from "../components/ui/confirm-dialog";
import StatusPill from "../components/StatusPill";
import { ago } from "../lib/time";
import { cn } from "../lib/utils";

export default function ProductPage() {
  const { productId = "" } = useParams();
  const detail = useQuery({
    queryKey: ["product", productId],
    queryFn: () =>
      unwrap(
        api.GET("/api/products/{product_id}", {
          params: { path: { product_id: productId } },
        }),
      ),
    refetchInterval: 4_000,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const attention = useAttention(productId, true);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const qc = useQueryClient();
  const archive = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/products/{product_id}/archive", {
          params: { path: { product_id: productId } },
        }),
      ),
    onSuccess: (o) => {
      setConfirmArchive(false);
      qc.invalidateQueries({ queryKey: ["product", productId] });
      qc.invalidateQueries({ queryKey: ["products"] });
      toast.success("Product archived", {
        description: `${o.title}: app removed, port freed`,
      });
    },
  });

  if (detail.isLoading)
    return <div className="h-40 animate-pulse rounded-xl bg-muted" />;
  if (detail.error) return <p className="error">{detail.error.message}</p>;
  const { product, changes, feedback, spend } = detail.data!;
  const changeId = selected ?? product.latest_change_id ?? changes[0]?.id;

  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-5">
      <Link
        to="/"
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground"
      >
        <ArrowLeft className="size-4" /> Products
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="m-0 text-2xl font-semibold tracking-tight">
            {product.title}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {product.slug} · created {ago(product.created_at)}
            {product.created_by ? ` by ${product.created_by}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {product.archived_at ? (
            <span className="pill muted">archived</span>
          ) : (
            product.latest_status && (
              <StatusPill status={product.latest_status} />
            )
          )}
          {!product.archived_at && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmArchive(true)}
              disabled={
                product.latest_status
                  ? ACTIVE.has(product.latest_status)
                  : false
              }
              title="Remove the app from the cluster and free its port"
            >
              <Archive /> Archive
            </Button>
          )}
        </div>
      </div>
      {!product.archived_at && (
        <NeedsYou
          productId={product.id}
          selectedChange={changeId}
          onSelectChange={setSelected}
        />
      )}
      {!product.archived_at && product.target !== "repo" && (
        <NextIteration
          productId={product.id}
          latestStatus={product.latest_status}
          onStarted={() => setSelected(null)}
        />
      )}
      {product.archived_at && (
        <p className="note m-0">
          Archived {ago(product.archived_at)}
          {product.archived_by ? ` by ${product.archived_by}` : ""}. The app was
          removed from the cluster and its port freed; the product repo, changes
          and evidence are kept. Create a new product to build it again.
        </p>
      )}
      <ConfirmDialog
        open={confirmArchive}
        onOpenChange={setConfirmArchive}
        title={`Archive “${product.title}”?`}
        confirm="Archive product"
        destructive
        busy={archive.isPending}
        onConfirm={() => archive.mutate()}
      >
        <p className="m-0">
          The running app{product.app_url ? ` at ${product.app_url}` : ""} is
          removed from the cluster and its port is freed for new products. No
          further iterations are possible.
        </p>
        <p className="m-0">
          Kept: the product repo, every change, its events and evidence.
        </p>
        <Problems error={archive.error} />
      </ConfirmDialog>

      <div className="grid grid-cols-2 items-start gap-3 lg:grid-cols-4">
        <Fact icon={<Layers />} label="Blueprint">
          {product.blueprint}
          {product.pack && (
            <span className="block text-xs font-normal text-muted-foreground">
              stack pack {product.pack}
            </span>
          )}
          {product.target === "repo" && (
            <span
              className="block whitespace-normal text-xs font-normal text-muted-foreground"
              data-testid="stack-guidance"
            >
              {(product.stack_matches ?? []).length === 0
                ? "no stack pack detected: generic mode"
                : (product.stack_matches ?? []).map((m) => (
                    <span key={`${m.id}:${m.folder}`} className="block">
                      {m.title || m.id} guidance in{" "}
                      {m.folder ? `${m.folder}/` : "root"}
                      {(m.egress ?? []).length > 0 &&
                        ` · egress ${(m.egress ?? []).join(", ")}`}
                    </span>
                  ))}
            </span>
          )}
        </Fact>
        {product.target === "repo" ? (
          <Fact icon={<GitBranch />} label="Branch">
            {product.repo_ref ?? (
              <span className="text-muted-foreground">
                default (found on clone)
              </span>
            )}
          </Fact>
        ) : (
          <Fact icon={<Globe />} label="App">
            {product.app_url ? (
              <a
                href={product.app_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-0.5"
              >
                {product.app_url.replace(/^https?:\/\//, "")}{" "}
                <ArrowUpRight className="size-3" />
              </a>
            ) : (
              <span className="text-muted-foreground">
                {product.host_port
                  ? `will be served on :${product.host_port}`
                  : "not deployed yet"}
              </span>
            )}
          </Fact>
        )}
        <Fact icon={<GitBranch />} label="Repository">
          {product.repo_url ? (
            <a
              href={product.repo_url}
              target="_blank"
              rel="noreferrer"
              className="truncate"
            >
              {product.repo_url.replace(/^https:\/\/github\.com\//, "")}
            </a>
          ) : (
            <span className="text-muted-foreground">local only</span>
          )}
        </Fact>
        <SpendCard spend={spend} />
      </div>

      <Panel
        id="requirements"
        icon={<FileText className="text-primary" />}
        title={
          product.target === "repo"
            ? "What the team wants to know"
            : "Requirements"
        }
        summary={
          <>
            <span className="line-clamp-1">
              {product.requirements.split("\n")[0]}
            </span>
            {feedback.length > 0 && (
              <span className="pill muted">
                {feedback.length} change request
                {feedback.length === 1 ? "" : "s"}
              </span>
            )}
          </>
        }
      >
        <pre className="pre m-0">{product.requirements}</pre>
        {feedback.length > 0 && (
          <div className="grid gap-1">
            <span className="text-sm font-medium">
              Change requests ({feedback.length})
            </span>
            <ol className="m-0 grid gap-2 pl-5 text-sm">
              {feedback.map((f) => (
                <li key={f.id}>{f.text}</li>
              ))}
            </ol>
          </div>
        )}
      </Panel>

      <WorkLedger
        productId={product.id}
        canChange={
          !product.archived_at &&
          !ACTIVE.has(String(product.latest_status ?? ""))
        }
        attention={(attention.data ?? []).some(
          (a) => a.kind === "accept_risk",
        )}
        onChanged={() =>
          qc.invalidateQueries({ queryKey: ["product", productId] })
        }
      />

      {changes.length > 1 && (
        <div
          className="flex w-fit flex-wrap gap-1 rounded-lg border bg-card p-1"
          role="tablist"
          aria-label="Iterations"
        >
          {changes.map((r) => (
            <button
              key={r.id}
              role="tab"
              aria-selected={r.id === changeId}
              className={cn("tab", r.id === changeId && "active")}
              onClick={() => setSelected(r.id)}
            >
              iteration {r.iteration}
              {r.kind && r.kind !== "new" && r.kind !== "feature"
                ? ` · ${KIND_LABEL[r.kind]}`
                : ""}
            </button>
          ))}
        </div>
      )}
      {changeId && (
        <ChangePanel
          key={changeId}
          changeId={changeId}
          productId={product.id}
          archived={!!product.archived_at}
          target={product.target}
        />
      )}
    </div>
  );
}

function Fact({
  icon,
  label,
  children,
}: {
  icon: ReactNode;
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-start gap-3 rounded-xl border bg-card px-4 py-3 shadow-sm">
      <span className="mt-0.5 text-muted-foreground [&_svg]:size-4">
        {icon}
      </span>
      <div className="min-w-0">
        <div className="text-xs font-medium text-muted-foreground">{label}</div>
        <div className="truncate text-sm">{children}</div>
      </div>
    </div>
  );
}
