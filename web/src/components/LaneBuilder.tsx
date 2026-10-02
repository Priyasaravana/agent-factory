import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  pointerWithin,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useState } from "react";
import type { CatalogView, DraftView, StationView } from "../api/client";

/** What the palette offers. Built-in agent handlers are listed only while unused. */
type PaletteItem = {
  key: string;
  kind: "agent" | "check";
  handler: string;
  label: string;
  hint: string;
  phase: string | null;
};
type HandlerInfo = NonNullable<CatalogView["handler_info"]>[string];
type PhaseInfo = NonNullable<CatalogView["phases"]>[number];

// The drop target is what the pointer is over; fall back to the nearest card
// (the dragged card's centre can be far from the grip that is held).
const pointerFirst: CollisionDetection = (args) => {
  const hits = pointerWithin(args);
  return hits.length ? hits : closestCenter(args);
};


export type NewStation = {
  id: string;
  kind: "agent" | "check";
  handler: string;
  agent?: string;
  on_fail?: string | null;
  only_on_fail?: boolean;
  next?: string | null;
  position: number;
};

export type LaneOps = {
  add: (s: NewStation) => void;
  remove: (id: string) => void;
  reorder: (order: string[]) => void;
  update: (id: string, patch: Record<string, string | boolean | null>) => void;
  busy: boolean;
};

function uniqueId(base: string, taken: string[]): string {
  if (!taken.includes(base)) return base;
  for (let i = 2; ; i++) if (!taken.includes(`${base}-${i}`)) return `${base}-${i}`;
}

export default function LaneBuilder({
  draft,
  handlers,
  info = {},
  phases = [],
  ops,
}: {
  draft: DraftView;
  handlers: Record<string, string[]>;
  info?: Record<string, HandlerInfo>;
  phases?: PhaseInfo[];
  ops: LaneOps;
}) {
  const stations = draft.stations;
  const ids = stations.map((s) => s.id);
  const agentIds = draft.agents.map((a) => a.spec.id);
  const usedHandlers = new Set(stations.map((s) => s.handler));
  const phaseIndex = (p: string | null | undefined) => {
    const i = phases.findIndex((x) => x.id === p);
    return i < 0 ? phases.length : i;
  };
  const item = (kind: "agent" | "check", h: string): PaletteItem => ({
    key: kind === "check" ? `check-${h}` : h,
    kind,
    handler: h,
    label: info[h]?.label ?? h,
    hint: info[h]?.hint ?? "",
    phase: info[h]?.phase ?? null,
  });
  // in loop order: plan → code → test → release → deploy → validate → handover
  const runOrder = Object.keys(info);
  const byPhase = (a: PaletteItem, b: PaletteItem) =>
    phaseIndex(a.phase) - phaseIndex(b.phase) || runOrder.indexOf(a.handler) - runOrder.indexOf(b.handler);
  const palette: PaletteItem[] = [
    item("agent", "agent"),
    ...[
      ...(handlers.agent ?? []).filter((h) => h !== "agent" && !usedHandlers.has(h)).map((h) => item("agent", h)),
      ...(handlers.check ?? []).map((h) => item("check", h)),
    ].sort(byPhase),
  ];
  const implementId = stations.find((s) => s.handler === "implement")?.id ?? null;

  const [pending, setPending] = useState<NewStation | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const startAdd = (item: PaletteItem, position: number) => {
    const base = item.handler === "agent" ? "custom-step" : item.handler;
    setPending({
      id: uniqueId(base, ids),
      kind: item.kind,
      handler: item.handler,
      agent: item.kind === "agent" ? agentIds[0] : undefined,
      on_fail: ["test", "quality-gate", "build"].includes(item.handler) ? implementId : null,
      only_on_fail: item.handler === "deploy-repair",
      next: null,
      position,
    });
  };

  const onDragStart = (e: DragStartEvent) => setDragging(String(e.active.id));
  const onDragEnd = (e: DragEndEvent) => {
    setDragging(null);
    const active = String(e.active.id);
    const over = e.over ? String(e.over.id) : null;
    if (!over) return;
    if (active.startsWith("palette:")) {
      const item = palette.find((p) => `palette:${p.key}` === active);
      if (!item) return;
      const pos = over === "lane-end" ? stations.length : Math.max(0, ids.indexOf(over));
      startAdd(item, pos);
      return;
    }
    const to = over === "lane-end" ? ids.length - 1 : ids.indexOf(over);
    if (active !== over && to >= 0) ops.reorder(arrayMove(ids, ids.indexOf(active), to));
  };

  const draggingItem = dragging?.startsWith("palette:") ? palette.find((p) => `palette:${p.key}` === dragging) : null;

  return (
    <DndContext sensors={sensors} collisionDetection={pointerFirst} onDragStart={onDragStart} onDragEnd={onDragEnd}>
      <div className="builder">
        <aside className="palette" aria-label="Station palette">
          <div className="small muted">Drag onto the lane, or click to add at the end</div>
          {palette.map((p) => (
            <PaletteTile key={p.key} item={p} onClick={() => startAdd(p, stations.length)} />
          ))}
        </aside>
        <div className="lane-wrap">
          <SortableContext items={ids} strategy={rectSortingStrategy}>
            <ol className="lane" aria-label="Workflow lane">
              {stations.map((s, i) => (
                <LaneCard
                  key={s.id}
                  s={s}
                  index={i}
                  ids={ids}
                  agentIds={agentIds}
                  phases={phases}
                  newPhase={i === 0 || stations[i - 1].phase !== s.phase}
                  ops={ops}
                  problems={draft.problems.filter((p) => p.includes(`'${s.id}'`))}
                />
              ))}
              <LaneEnd />
            </ol>
          </SortableContext>
          <p className="muted small">
            Stations run left to right, grouped by DevOps phase. <span className="route fail">↩</span> is where a
            failure goes back to. Repair
            stations (dashed) run only when routed to, then continue at <em>next</em>. Drag the ⠿ handle to reorder.
          </p>
        </div>
      </div>
      <DragOverlay>
        {draggingItem ? (
          <div className="tile overlay">
            <strong>{draggingItem.label}</strong>
          </div>
        ) : null}
      </DragOverlay>
      {pending && (
        <NewStationForm
          value={pending}
          ids={ids}
          agentIds={agentIds}
          busy={ops.busy}
          onChange={setPending}
          onCancel={() => setPending(null)}
          onSubmit={() => {
            ops.add(pending);
            setPending(null);
          }}
        />
      )}
    </DndContext>
  );
}

function PaletteTile({ item, onClick }: { item: PaletteItem; onClick: () => void }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: `palette:${item.key}` });
  return (
    <button
      ref={setNodeRef}
      type="button"
      className={`tile ${item.kind} ${isDragging ? "dragging" : ""}`}
      data-testid={`palette-${item.key}`}
      title={item.hint}
      onClick={onClick}
      {...listeners}
      {...attributes}
    >
      <strong>{item.label}</strong>
      <span className="small muted">
        {item.kind === "agent" ? "agent" : "check"}
        {item.phase ? ` · ${item.phase}` : ""}
      </span>
    </button>
  );
}

function LaneEnd() {
  const { setNodeRef, isOver } = useDroppable({ id: "lane-end" });
  return (
    <li ref={setNodeRef} className={`lane-end ${isOver ? "over" : ""}`} data-testid="lane-end">
      drop here
    </li>
  );
}

function LaneCard({
  s,
  index,
  ids,
  agentIds,
  phases,
  newPhase,
  ops,
  problems,
}: {
  s: StationView;
  index: number;
  ids: string[];
  agentIds: string[];
  phases: PhaseInfo[];
  newPhase: boolean;
  ops: LaneOps;
  problems: string[];
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging, isOver } =
    useSortable({ id: s.id });
  const style = { transform: CSS.Transform.toString(transform), transition };
  const others = ids.filter((i) => i !== s.id);
  return (
    <li
      ref={setNodeRef}
      style={style}
      className={`lane-card ${s.kind} ${s.repair ? "repair" : ""} ${isDragging ? "dragging" : ""} ${
        isOver ? "over" : ""
      } ${problems.length ? "bad" : ""}`}
      data-testid={`station-${s.id}`}
    >
      <div className="row spread">
        <button
          ref={setActivatorNodeRef}
          className="grip"
          aria-label={`Move ${s.id}`}
          type="button"
          {...listeners}
          {...attributes}
        >
          ⠿
        </button>
        <span className="small muted">{index + 1}</span>
        <button
          className="icon"
          type="button"
          aria-label={`Remove ${s.id}`}
          disabled={ops.busy}
          onClick={() => {
            if (window.confirm(`Remove station '${s.id}'? Routes that point at it are cleared.`)) ops.remove(s.id);
          }}
        >
          ×
        </button>
      </div>
      <span className={`small phase ${newPhase ? "" : "muted"}`} data-testid={`phase-of-${s.id}`}>
        {phases.find((p) => p.id === s.phase)?.label ?? s.phase}
      </span>
      <strong className="name">{s.label || s.id}</strong>
      <span className="small muted">
        {s.kind === "agent" ? "agent" : "check"}
        {s.label && s.label.toLowerCase().replace(/ /g, "-") !== s.id ? ` · ${s.id}` : ""}
      </span>
      {s.handler === "agent" && phases.length > 0 && (
        <label className="small">
          phase
          <select
            aria-label={`${s.id} phase`}
            value={s.phase}
            onChange={(e) => ops.update(s.id, { phase: e.target.value })}
          >
            {phases.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {s.kind === "agent" && (
        <label className="small">
          agent
          <select
            aria-label={`${s.id} agent`}
            value={s.role ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              ops.update(s.id, { agent: v });
            }}
          >
            {!s.role && <option value="">— choose —</option>}
            {agentIds.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="small">
        <span className="route fail">↩</span> on fail
        <select
          aria-label={`${s.id} on fail`}
          value={s.on_fail ?? ""}
          onChange={(e) => {
            const v = e.target.value;
            ops.update(s.id, { on_fail: v || null });
          }}
        >
          <option value="">stop (hold)</option>
          {others.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
      <label className="small check">
        <input
          type="checkbox"
          checked={s.repair}
          onChange={(e) => {
            const v = e.target.checked;
            ops.update(s.id, { only_on_fail: v });
          }}
        />
        repair only
      </label>
      {s.repair && (
        <label className="small">
          then
          <select
            aria-label={`${s.id} next`}
            value={s.next ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              ops.update(s.id, { next: v || null });
            }}
          >
            <option value="">— choose —</option>
            {others.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
      )}
      {problems.map((p) => (
        <span key={p} className="small error">
          {p}
        </span>
      ))}
    </li>
  );
}

function NewStationForm({
  value,
  ids,
  agentIds,
  busy,
  onChange,
  onCancel,
  onSubmit,
}: {
  value: NewStation;
  ids: string[];
  agentIds: string[];
  busy: boolean;
  onChange: (v: NewStation) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const idOk = /^[a-z][a-z0-9_-]{1,40}$/.test(value.id) && !ids.includes(value.id);
  return (
    <div className="card new-station" role="dialog" aria-label="Add station">
      <h4>
        Add {value.kind === "agent" ? "agent station" : "check"} <code>{value.handler}</code> at position{" "}
        {value.position + 1}
      </h4>
      <div className="row">
        <label>
          station id
          <input
            aria-label="station id"
            value={value.id}
            onChange={(e) => onChange({ ...value, id: e.target.value.trim() })}
          />
        </label>
        {value.kind === "agent" && (
          <label>
            agent
            <select
              aria-label="new station agent"
              value={value.agent ?? ""}
              onChange={(e) => onChange({ ...value, agent: e.target.value })}
            >
              {agentIds.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          on fail
          <select
            aria-label="new station on fail"
            value={value.on_fail ?? ""}
            onChange={(e) => onChange({ ...value, on_fail: e.target.value || null })}
          >
            <option value="">stop (hold)</option>
            {ids.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!idOk && <p className="error small">Use 2–41 lowercase letters, digits, - or _, not already used.</p>}
      <div className="row">
        <button type="button" disabled={!idOk || busy} onClick={onSubmit}>
          Add station
        </button>
        <button type="button" className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
