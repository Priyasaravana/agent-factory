import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  rectSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  CornerDownLeft,
  GripVertical,
  Lock,
  Package,
  Plus,
  ShieldCheck,
  SquareCheck,
  Wrench,
  X,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  KIND_LABEL,
  api,
  unwrap,
  type CatalogView,
  type ChangeKind,
  type DraftView,
  type StationView,
} from "../api/client";
import type { components } from "../api/schema";
import { cn } from "../lib/utils";
import { ConfirmDialog } from "./ui/confirm-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { Tip } from "./ui/tooltip";

type Phase = NonNullable<CatalogView["phases"]>[number];
type Template = components["schemas"]["StationTemplateView"];
type Preset = NonNullable<CatalogView["scope_presets"]>[number];
type Patch = Record<string, string | boolean | string[] | null>;

/** What the board changes: every call returns the new draft. */
export type BoardOps = {
  run: (fn: () => Promise<DraftView>) => void;
  apply: (d: DraftView) => void;
  busy: boolean;
  editAgent: (id: string) => void;
};

const CHECKS = [
  { id: "secrets", label: "Secrets" },
  { id: "code", label: "Code" },
  { id: "dependencies", label: "Dependencies" },
  { id: "config", label: "Config (Dockerfile, Kubernetes, IaC)" },
];
const PROVIDERS = [
  { id: "", label: "Factory default" },
  { id: "local", label: "Open-source tool" },
  { id: "github", label: "GitHub" },
  { id: "snyk", label: "Snyk" },
  { id: "agent", label: "Factory agent" },
];
const FAIL_ON = [
  { id: "critical", label: "Critical findings" },
  { id: "high", label: "High and above" },
  { id: "medium", label: "Medium and above" },
  { id: "low", label: "Low and above" },
  { id: "none", label: "Never (report only)" },
];
const WORKS_ON: Record<string, string> = { factory: "New builds", repo: "Your existing repos" };

const ARTIFACTS: Record<string, string> = {
  request: "The request",
  branch: "The code",
  spec: "Spec",
  design: "Design",
  stories: "Stories",
  image: "Container image",
  url: "Running app",
  pr: "Pull request",
};
/** An artifact reference (`report.test@1`, `design@1 | request@1`) as people read it. */
const artifactTitle = (ref: string) =>
  ref
    .split(" | ")
    .map((r) => {
      const key = r.split("@")[0];
      if (ARTIFACTS[key]) return ARTIFACTS[key];
      const kind = key.startsWith("report.") ? key.slice(7).replace(/-/g, " ") : key;
      return `${kind.charAt(0).toUpperCase()}${kind.slice(1)} report`;
    })
    .join(" or ");

// what a phase allows, in a word or two (the tooltip has the full sentence)
const SHORT_CAP: Record<string, string> = {
  "read-repo": "read code",
  "write-docs": "write docs",
  "write-tickets": "write tickets",
  "write-code": "change code",
  "run-sandbox": "run commands",
  "run-scanners": "run scanners",
  "push-image": "push images",
  deploy: "deploy",
  promote: "promote",
  "open-pr": "open pull requests",
  "read-metrics": "read metrics",
  "read-logs": "read logs",
  "open-incident": "open incidents",
  rollback: "roll back",
  approve: "record approvals",
};
const shortCap = (c: string) => SHORT_CAP[c.split(":")[0]] ?? c;
const CAP_ORDER = Object.keys(SHORT_CAP);
const byCap = (a: string, b: string) => CAP_ORDER.indexOf(a.split(":")[0]) - CAP_ORDER.indexOf(b.split(":")[0]);

/** Problems that name this station, in its own words. */
const problemsOf = (problems: string[], id: string) => problems.filter((p) => p.includes(`'${id}'`));

export default function PhaseBoard({
  workflowId,
  draft,
  catalog,
  ops,
}: {
  workflowId: string;
  draft: DraftView;
  catalog: CatalogView;
  ops: BoardOps;
}) {
  const wid = { workflow_id: workflowId };
  const phases = catalog.phases ?? [];
  const span = draft.span ?? [];
  const [open, setOpen] = useState<string | null>(null);
  const templates = useQuery({
    queryKey: ["draft-templates", workflowId, draft.updated_at, draft.stations.length],
    queryFn: () => unwrap(api.GET("/api/workflows/{workflow_id}/draft/templates", { params: { path: wid } })),
  });

  const byPhase = (pid: string) => draft.stations.filter((s) => s.phase === pid);
  const addFrom = (t: Template, phase: string) => {
    const before = new Set(draft.stations.map((s) => s.id));
    ops.run(async () => {
      const d = await unwrap(
        api.POST("/api/workflows/{workflow_id}/draft/stations/from-template", {
          params: { path: wid },
          body: { template: t.id, phase },
        }),
      );
      const added = d.stations.find((s) => !before.has(s.id));
      if (added) {
        toast.success(`Added ${t.title} to ${phases.find((p) => p.id === phase)?.label ?? phase}`);
        if (t.id === "custom" || t.adds_problems?.length) setOpen(added.id);
      }
      return d;
    });
  };
  const update = (id: string, patch: Patch) =>
    ops.run(() =>
      unwrap(
        api.PATCH("/api/workflows/{workflow_id}/draft/stations/{station_id}", {
          params: { path: { ...wid, station_id: id } },
          body: patch,
        }),
      ),
    );
  const remove = (id: string) => {
    setOpen(null);
    ops.run(() =>
      unwrap(
        api.DELETE("/api/workflows/{workflow_id}/draft/stations/{station_id}", {
          params: { path: { ...wid, station_id: id } },
        }),
      ),
    );
  };
  const reorder = (order: string[]) => {
    ops.apply({ ...draft, stations: order.map((id) => draft.stations.find((s) => s.id === id)!) });
    ops.run(() =>
      unwrap(api.PUT("/api/workflows/{workflow_id}/draft/stations/order", { params: { path: wid }, body: { order } })),
    );
  };

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  // stations move only within their own phase box: the order across boxes is the phases' order
  const onDragEnd = (e: DragEndEvent) => {
    const a = String(e.active.id);
    const o = e.over ? String(e.over.id) : null;
    if (!o || a === o) return;
    const sa = draft.stations.find((s) => s.id === a);
    const so = draft.stations.find((s) => s.id === o);
    if (!sa || !so || sa.phase !== so.phase) return;
    const ids = draft.stations.map((s) => s.id);
    reorder(arrayMove(ids, ids.indexOf(a), ids.indexOf(o)));
  };

  const laneProblems = draft.problems.filter((p) => !draft.stations.some((s) => p.includes(`'${s.id}'`)));
  const selected = draft.stations.find((s) => s.id === open) ?? null;

  return (
    <div className="grid gap-4">
      <LaneScope workflowId={workflowId} draft={draft} catalog={catalog} ops={ops} templates={templates.data?.phases} />

      {laneProblems.length > 0 && (
        <div className="problems" role="status">
          <strong>The lane as a whole</strong>
          <ul className="mt-1 list-disc pl-5">
            {laneProblems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <ol className="board" aria-label="Phases of the lane">
          {groups(phases, (p) => span.includes(p.id) || byPhase(p.id).length > 0).map((g) =>
            !g.shown ? (
              <li key={g.items[0].id} className="phase-box collapsed">
                <span className="text-sm">{g.items.map((p) => p.label).join(", ")}</span>
                <span className="small muted">not in this lane</span>
              </li>
            ) : (
              g.items.map((p) => (
                <PhaseBox
                  key={p.id}
                  phase={p}
                  inScope={span.includes(p.id)}
                  stations={byPhase(p.id)}
                  problems={draft.problems}
                  templates={templates.data?.phases?.[p.id] ?? []}
                  loading={templates.isLoading}
                  capabilities={catalog.capabilities ?? {}}
                  busy={ops.busy}
                  onAdd={(t) => addFrom(t, p.id)}
                  onOpen={setOpen}
                />
              ))
            ),
          )}
        </ol>
      </DndContext>
      <p className="muted small">
        Each box is a phase. A station can only be added to its own phase and gets only what that phase allows. Drag{" "}
        <GripVertical className="inline size-3.5 align-[-2px]" /> to change the order inside a box; click a station to
        change how it runs.
      </p>

      <StationDrawer
        station={selected}
        draft={draft}
        phases={phases}
        span={span}
        capabilities={catalog.capabilities ?? {}}
        busy={ops.busy}
        onClose={() => setOpen(null)}
        onUpdate={update}
        onRemove={remove}
        onEditAgent={ops.editAgent}
      />
    </div>
  );
}

/* ------------------------------------------------------------ scope bar -- */
function LaneScope({
  workflowId,
  draft,
  catalog,
  ops,
  templates,
}: {
  workflowId: string;
  draft: DraftView;
  catalog: CatalogView;
  ops: BoardOps;
  templates?: Record<string, Template[]>;
}) {
  const wid = { workflow_id: workflowId };
  const phases = catalog.phases ?? [];
  const ids = phases.map((p) => p.id);
  const span = draft.span ?? [];
  const presets = catalog.scope_presets ?? [];
  const [asking, setAsking] = useState<Preset | null>(null);
  const title = (pid: string) => phases.find((p) => p.id === pid)?.label ?? pid;
  // the preset's stations this lane doesn't have yet (the ones applying it would add)
  const missing = (p: Preset) =>
    p.adds
      .map((tid) =>
        Object.values(templates ?? {})
          .flat()
          .find((t) => t.id === tid),
      )
      .filter((t): t is Template => !!t && !t.present)
      .map((t) => t.title);

  const setScope = (scope: string[] | null, entry?: string[]) =>
    ops.run(() =>
      unwrap(
        api.PATCH("/api/workflows/{workflow_id}/draft/settings", {
          params: { path: wid },
          body: { scope: scope ?? undefined, entry },
        }),
      ),
    );
  // a phase outside the scope extends it to there; an end of the scope pulls it in by one
  const togglePhase = (pid: string) => {
    if (!span.length) return setScope([pid, pid]);
    const first = ids.indexOf(span[0]);
    const last = ids.indexOf(span[span.length - 1]);
    const i = ids.indexOf(pid);
    if (i < first) return setScope([pid, span[span.length - 1]]);
    if (i > last) return setScope([span[0], pid]);
    if (span.length === 1) return;
    if (i === first) return setScope([ids[first + 1], span[span.length - 1]]);
    if (i === last) return setScope([span[0], ids[last - 1]]);
  };
  const leaving = (p: Preset) => {
    if (!p.scope.length) return [];
    const [a, b] = [ids.indexOf(p.scope[0]), ids.indexOf(p.scope[p.scope.length - 1])];
    const keep = new Set(ids.slice(a, b + 1));
    return draft.stations.filter((s) => !keep.has(s.phase));
  };
  const applyPreset = (p: Preset) => {
    setAsking(null);
    ops.run(async () => {
      const r = await unwrap(
        api.POST("/api/workflows/{workflow_id}/draft/preset", { params: { path: wid }, body: { preset: p.id } }),
      );
      const parts = [
        r.removed.length ? `removed ${r.removed.length}` : "",
        r.added.length ? `added ${r.added.join(", ")}` : "",
      ].filter(Boolean);
      toast.success(`Scoped to ${p.title}${parts.length ? `: ${parts.join("; ")}` : ""}`);
      return r.draft;
    });
  };
  const entry = draft.entry ?? [];
  const yours = entry.filter((e) => e.by === "your tools");
  const choices = (catalog.entry_choices ?? []).filter((c) => !entry.some((e) => e.key === c.key));
  const setEntry = (refs: string[]) => setScope(null, refs);

  return (
    <div className="scope">
      <div className="scope-head">
        <div className="grid gap-0.5">
          <span className="text-sm font-semibold">{WORKS_ON[draft.works_on ?? "factory"]}</span>
          <span className="small muted">
            For {(draft.kinds ?? []).map((k) => KIND_LABEL[k as ChangeKind] ?? k).join(", ") || "no"} changes
            {span.length === 1 && <>, in {title(span[0])} only</>}
            {span.length > 1 && (
              <>
                , from {title(span[0])} to {title(span[span.length - 1])}
              </>
            )}
          </span>
        </div>
        <div className="presets" role="radiogroup" aria-label="Scope preset">
          {presets.map((p) => (
            <Tip key={p.id} label={p.hint} side="bottom">
              <button
                type="button"
                role="radio"
                aria-checked={draft.preset === p.id}
                className={cn("preset", draft.preset === p.id && "on")}
                disabled={ops.busy}
                onClick={() => (draft.preset === p.id ? null : setAsking(p))}
              >
                {p.title}
              </button>
            </Tip>
          ))}
        </div>
      </div>

      <div className="rail" aria-label="Phases this lane covers">
        {phases.map((p) => {
          const on = span.includes(p.id);
          const end = on && (p.id === span[0] || p.id === span[span.length - 1]) && span.length > 1;
          return (
            <Tip
              key={p.id}
              side="bottom"
              label={
                on
                  ? end
                    ? `In scope. Click to stop the lane before ${p.label}.`
                    : "In scope"
                  : `Not in this lane. Click to extend the lane to ${p.label}.`
              }
            >
              <button
                type="button"
                className={cn("rail-step", on && "on")}
                aria-pressed={on}
                disabled={ops.busy}
                onClick={() => togglePhase(p.id)}
              >
                {p.label}
                {p.source !== "built-in" && <Package className="size-3 opacity-70" aria-label={`from ${p.source}`} />}
              </button>
            </Tip>
          );
        })}
      </div>

      <div className="entry">
        <span className="small muted">Starts from</span>
        {entry.map((e) =>
          e.by === "factory" ? (
            <Tip key={e.ref} label="The factory provides this with every change" side="bottom">
              <span className="pill muted">
                <Lock /> {e.title}
              </span>
            </Tip>
          ) : (
            <span key={e.ref} className="pill info">
              {e.title}
              <span className="opacity-70">· your tools</span>
              <button
                type="button"
                className="entry-x"
                aria-label={`Stop starting from ${e.title}`}
                disabled={ops.busy}
                onClick={() => setEntry(yours.filter((y) => y.ref !== e.ref).map((y) => y.ref))}
              >
                <X className="size-3" />
              </button>
            </span>
          ),
        )}
        {choices.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button type="button" className="add-entry" disabled={ops.busy}>
                <Plus className="size-3.5" /> Add
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuLabel>Each change brings this from your own tools</DropdownMenuLabel>
              {choices.map((c) => (
                <DropdownMenuItem key={c.ref} onSelect={() => setEntry([...yours.map((y) => y.ref), c.ref])}>
                  {c.title}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      <ConfirmDialog
        open={asking !== null}
        onOpenChange={(v) => !v && setAsking(null)}
        title={asking ? `Scope this lane to ${asking.title}?` : ""}
        confirm={asking ? `Use ${asking.title}` : "Use"}
        busy={ops.busy}
        destructive={asking ? leaving(asking).length > 0 : false}
        onConfirm={() => asking && applyPreset(asking)}
      >
        {asking && (
          <>
            <p>{asking.hint}</p>
            {leaving(asking).length > 0 && (
              <p>
                <strong className="text-foreground">Removed:</strong>{" "}
                {leaving(asking)
                  .map((s) => s.label || s.id)
                  .join(", ")}
                , and any station left without what it needs.
              </p>
            )}
            {missing(asking).length > 0 && (
              <p>
                <strong className="text-foreground">Added:</strong> {missing(asking).join(", ")}.
              </p>
            )}
            {asking.entry.length > 0 && (
              <p>
                <strong className="text-foreground">Each change then brings:</strong>{" "}
                {asking.entry
                  .map((r, i) => {
                    const t = catalog.entry_choices?.find((c) => c.ref === r)?.title ?? r;
                    return i ? t.toLowerCase() : t;
                  })
                  .join(" and ")}{" "}
                from your own tools.
              </p>
            )}
            {asking.id === "full" && (
              <p>
                <strong className="text-foreground">Brought back:</strong> every station of the factory default lane
                this lane is missing, with its agent and routes. Your own custom steps stay.
              </p>
            )}
            <p>Nothing is live until you publish the draft.</p>
          </>
        )}
      </ConfirmDialog>
    </div>
  );
}

/* ------------------------------------------------------------ phase box -- */
/** Runs of adjacent phases that are shown, or folded into one "not in this lane" row. */
function groups(phases: Phase[], shown: (p: Phase) => boolean) {
  const out: { shown: boolean; items: Phase[] }[] = [];
  for (const p of phases) {
    const v = shown(p);
    const last = out[out.length - 1];
    if (last && last.shown === v) last.items.push(p);
    else out.push({ shown: v, items: [p] });
  }
  return out;
}

function PhaseBox({
  phase,
  inScope,
  stations,
  problems,
  templates,
  loading,
  capabilities,
  busy,
  onAdd,
  onOpen,
}: {
  phase: Phase;
  inScope: boolean;
  stations: StationView[];
  problems: string[];
  templates: Template[];
  loading: boolean;
  capabilities: Record<string, string>;
  busy: boolean;
  onAdd: (t: Template) => void;
  onOpen: (id: string) => void;
}) {
  const allows = [...(phase.allows ?? [])].sort(byCap);
  const builtin = templates.filter((t) => t.source === "built-in" && t.id !== "custom");
  const packs = templates.filter((t) => t.source !== "built-in");
  const custom = templates.find((t) => t.id === "custom");
  return (
    <li className={cn("phase-box", !inScope && "outside")} aria-label={phase.label}>
      <header className="phase-head">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="m-0 text-[15px]">{phase.label}</h4>
          {phase.source !== "built-in" && (
            <span className="pill muted" title={`Added by ${phase.source}`}>
              {phase.source === "org" ? "your org" : phase.source.split("@")[0]}
            </span>
          )}
        </div>
        <Tip
          side="bottom"
          label={
            <ul className="grid max-w-64 gap-0.5">
              {allows.map((a) => (
                <li key={a}>{capabilities[a.split(":")[0]] ?? a}</li>
              ))}
            </ul>
          }
        >
          <span className="cursor-help text-xs text-muted-foreground">
            Can {allows.map(shortCap).join(", ") || "only look"}
          </span>
        </Tip>
        {!inScope && <span className="text-xs text-warn">Outside the lane's scope: move or remove these</span>}
      </header>

      <SortableContext items={stations.map((s) => s.id)} strategy={rectSortingStrategy}>
        <ul className="phase-stations">
          {stations.map((s) => (
            <StationCard key={s.id} s={s} problems={problemsOf(problems, s.id)} onOpen={() => onOpen(s.id)} />
          ))}
          {inScope && (
            <li className="grid">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button type="button" className="add-station" disabled={busy || loading}>
                    <Plus className="size-4" /> Add station
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-80 max-w-[92vw]">
                  <DropdownMenuLabel>Stations for {phase.label}</DropdownMenuLabel>
                  {builtin.map((t) => (
                    <TemplateItem key={t.id} t={t} onSelect={() => onAdd(t)} />
                  ))}
                  {packs.length > 0 && <DropdownMenuSeparator />}
                  {packs.map((t) => (
                    <TemplateItem key={t.id} t={t} onSelect={() => onAdd(t)} />
                  ))}
                  {custom && (
                    <>
                      <DropdownMenuSeparator />
                      <TemplateItem t={custom} onSelect={() => onAdd(custom)} />
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          )}
        </ul>
      </SortableContext>
    </li>
  );
}

function TemplateItem({ t, onSelect }: { t: Template; onSelect: () => void }) {
  const warn = t.adds_problems?.[0];
  return (
    <DropdownMenuItem onSelect={onSelect} className="items-start py-2">
      <span className="mt-0.5 text-muted-foreground">
        {t.control ? <ShieldCheck /> : t.kind === "agent" ? <Bot /> : <SquareCheck />}
      </span>
      <span className="grid min-w-0 gap-0.5">
        <span className="flex flex-wrap items-center gap-1.5 font-medium">
          {t.title}
          {t.present && <span className="pill muted">in the lane</span>}
          {t.source !== "built-in" && <span className="pill info">{t.source.split("@")[0]}</span>}
        </span>
        <span className="text-xs text-muted-foreground whitespace-normal">{t.hint}</span>
        {warn && (
          <span className="flex gap-1 text-xs text-warn whitespace-normal">
            <AlertTriangle className="mt-px size-3.5 shrink-0" />
            {warn}
          </span>
        )}
      </span>
    </DropdownMenuItem>
  );
}

function StationCard({ s, problems, onOpen }: { s: StationView; problems: string[]; onOpen: () => void }) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({
    id: s.id,
  });
  const detail =
    s.handler === "scan"
      ? `checks ${s.check ?? "?"} · fails on ${s.fail_on ?? "high"}`
      : s.kind === "agent"
        ? s.role
          ? `agent: ${s.role}`
          : "no agent yet"
        : "automatic check";
  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn("st-card", s.repair && "repair", problems.length > 0 && "bad", isDragging && "dragging")}
      data-testid={`station-${s.id}`}
    >
      <button
        ref={setActivatorNodeRef}
        type="button"
        className="grip"
        aria-label={`Move ${s.label || s.id}`}
        {...listeners}
        {...attributes}
      >
        <GripVertical className="size-4" />
      </button>
      <button type="button" className="st-open" onClick={onOpen} aria-label={`Open ${s.label || s.id}`}>
        <span className="flex items-center gap-1.5">
          <span className="text-muted-foreground">
            {s.control ? (
              <ShieldCheck className="size-4 text-ok" />
            ) : s.kind === "agent" ? (
              <Bot className="size-4" />
            ) : (
              <SquareCheck className="size-4" />
            )}
          </span>
          <strong className="truncate text-sm">{s.label || s.id}</strong>
          {problems.length > 0 && (
            <span className="pill bad ml-auto" title={problems.join("\n")}>
              {problems.length}
            </span>
          )}
        </span>
        <span className="truncate text-xs text-muted-foreground">{detail}</span>
        {(s.control || s.on_fail || s.repair) && (
          <span className="flex flex-wrap gap-1">
            {s.control && <span className="pill ok">required</span>}
            {s.repair && (
              <span className="pill muted">
                <Wrench /> repair{s.next ? ` → ${s.next}` : ""}
              </span>
            )}
            {s.on_fail && (
              <span className="pill warn">
                <CornerDownLeft /> {s.on_fail}
              </span>
            )}
          </span>
        )}
      </button>
    </li>
  );
}

/* --------------------------------------------------------------- drawer -- */
function StationDrawer({
  station: s,
  draft,
  phases,
  span,
  capabilities,
  busy,
  onClose,
  onUpdate,
  onRemove,
  onEditAgent,
}: {
  station: StationView | null;
  draft: DraftView;
  phases: Phase[];
  span: string[];
  capabilities: Record<string, string>;
  busy: boolean;
  onClose: () => void;
  onUpdate: (id: string, patch: Patch) => void;
  onRemove: (id: string) => void;
  onEditAgent: (id: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const agents = draft.agents.map((a) => a.spec.id);
  const others = draft.stations.filter((o) => o.id !== s?.id);
  const phase = phases.find((p) => p.id === s?.phase);
  const problems = s ? problemsOf(draft.problems, s.id) : [];
  const extra = (phase?.allows ?? []).filter((c) => c !== "read-repo");
  const set = (patch: Patch) => s && onUpdate(s.id, patch);
  const title = artifactTitle;

  return (
    <Dialog.Root open={s !== null} onOpenChange={(v) => !v && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content className="drawer" aria-describedby={undefined}>
          {s && (
            <>
              <div className="flex items-start justify-between gap-3">
                <div className="grid gap-0.5">
                  <Dialog.Title className="m-0 text-lg font-semibold">{s.label || s.id}</Dialog.Title>
                  <span className="small muted">
                    {phase?.label ?? s.phase} · {s.custom ? "your step" : s.kind === "agent" ? "agent" : "check"} ·{" "}
                    <code>{s.id}</code>
                  </span>
                </div>
                <Dialog.Close asChild>
                  <button type="button" className="icon" aria-label="Close">
                    <X className="size-4" />
                  </button>
                </Dialog.Close>
              </div>

              {s.control && (
                <p className="note">
                  A required control: when the code changes, a test, a secret scan and a change-risk check must run before
                  anything leaves the lane.
                </p>
              )}
              {problems.length > 0 && (
                <div className="problems">
                  <ul className="list-disc pl-5">
                    {problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                </div>
              )}

              {(s.kind === "agent" || s.handler === "scan") && (
                <section className="grid gap-2">
                  <label>
                    {s.handler === "scan" ? "Agent if the tool can't answer" : "Agent"}
                    <select
                      value={s.role ?? ""}
                      disabled={busy}
                      onChange={(e) => set({ agent: e.target.value })}
                    >
                      {!s.role && <option value="">Choose an agent</option>}
                      {agents.map((a) => (
                        <option key={a} value={a}>
                          {a}
                        </option>
                      ))}
                    </select>
                  </label>
                  {s.role && (
                    <button type="button" className="secondary justify-self-start small-btn" onClick={() => onEditAgent(s.role!)}>
                      {s.custom ? "Write what this step does" : `Edit agent ${s.role}`}
                    </button>
                  )}
                </section>
              )}

              {s.handler === "scan" && (
                <section className="grid gap-3 sm:grid-cols-2">
                  <label className="sm:col-span-2">
                    What it checks
                    <select value={s.check ?? ""} disabled={busy} onChange={(e) => set({ check: e.target.value })}>
                      {CHECKS.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Answered by
                    <select
                      value={s.provider ?? ""}
                      disabled={busy}
                      onChange={(e) => set({ provider: e.target.value || null })}
                    >
                      {PROVIDERS.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Fails on
                    <select value={s.fail_on ?? "high"} disabled={busy} onChange={(e) => set({ fail_on: e.target.value })}>
                      {FAIL_ON.map((f) => (
                        <option key={f.id} value={f.id}>
                          {f.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </section>
              )}

              <section className="grid gap-2">
                <label>
                  If it fails
                  <select
                    value={s.on_fail ?? ""}
                    disabled={busy}
                    onChange={(e) => set({ on_fail: e.target.value || null })}
                  >
                    <option value="">Stop and wait for a person</option>
                    {others.map((o) => (
                      <option key={o.id} value={o.id}>
                        Go back to {o.label || o.id}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={s.repair}
                    disabled={busy}
                    onChange={(e) => set({ only_on_fail: e.target.checked })}
                  />
                  <span>
                    Repair step
                    <span className="block text-xs font-normal text-muted-foreground">
                      Runs only when another station sends a failure here.
                    </span>
                  </span>
                </label>
                {s.repair && (
                  <label>
                    Then continue at
                    <select value={s.next ?? ""} disabled={busy} onChange={(e) => set({ next: e.target.value || null })}>
                      <option value="">Choose a station</option>
                      {others.map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.label || o.id}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </section>

              {s.custom && (
                <section className="grid gap-2">
                  <label>
                    Phase
                    <select value={s.phase} disabled={busy} onChange={(e) => set({ phase: e.target.value })}>
                      {phases
                        .filter((p) => span.includes(p.id))
                        .map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.label}
                          </option>
                        ))}
                    </select>
                  </label>
                  <fieldset>
                    <legend>What it may do</legend>
                    <label className="check">
                      <input type="checkbox" checked disabled /> <span>{capabilities["read-repo"]}</span>
                    </label>
                    {extra.map((c) => (
                      <label key={c} className="check">
                        <input
                          type="checkbox"
                          checked={s.uses?.includes(c) ?? false}
                          disabled={busy}
                          onChange={(e) => {
                            const uses = new Set(s.uses ?? ["read-repo"]);
                            if (e.target.checked) uses.add(c);
                            else uses.delete(c);
                            uses.add("read-repo");
                            set({ uses: [...uses] });
                          }}
                        />
                        <span>{capabilities[c.split(":")[0]] ?? c}</span>
                      </label>
                    ))}
                    {extra.length === 0 && <span className="small muted">{phase?.label} only lets a step look.</span>}
                  </fieldset>
                </section>
              )}

              {(s.needs?.length || s.gives?.length) && (
                <section className="grid gap-1.5 text-sm">
                  {s.needs && s.needs.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="small muted w-14">Needs</span>
                      {s.needs.map((n) => (
                        <span key={n} className="pill muted">
                          {title(n)}
                        </span>
                      ))}
                    </div>
                  )}
                  {s.gives && s.gives.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="small muted w-14">Gives</span>
                      {s.gives.map((g) => (
                        <span key={g} className="pill info">
                          {title(g)}
                        </span>
                      ))}
                    </div>
                  )}
                </section>
              )}

              <div className="mt-auto flex justify-end border-t pt-3">
                <button type="button" className="secondary text-bad" disabled={busy} onClick={() => setConfirming(true)}>
                  Remove station
                </button>
              </div>
              <ConfirmDialog
                open={confirming}
                onOpenChange={setConfirming}
                title={`Remove ${s.label || s.id}?`}
                confirm="Remove station"
                destructive
                busy={busy}
                onConfirm={() => {
                  setConfirming(false);
                  onRemove(s.id);
                }}
              >
                <p>Stations that send failures here will stop and wait for a person instead.</p>
              </ConfirmDialog>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
