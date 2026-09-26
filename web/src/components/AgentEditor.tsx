import { useState } from "react";
import type { AgentSpec, CatalogView, RefDoc } from "../api/client";

const BLANK: AgentSpec = {
  id: "",
  description: "",
  model: "default",
  tools: "observer",
  extra_tools: [],
  disallowed_tools: [],
  skills: ["factory-station-contract"],
  max_turns: 40,
  produces: [],
  context_docs: [],
  previous_iterations: 0,
  learnings: "",
  prompt: "# Role: …\n\nDescribe the job, what good looks like, and what evidence to return.\n",
};

type Props = {
  initial?: AgentSpec;
  catalog: CatalogView;
  docs: RefDoc[];
  saving: boolean;
  error?: string;
  onSave: (spec: AgentSpec) => void;
  onCancel: () => void;
};

export default function AgentEditor({ initial, catalog, docs, saving, error, onSave, onCancel }: Props) {
  const isNew = !initial;
  const [s, setS] = useState<AgentSpec>(initial ?? BLANK);
  const set = <K extends keyof AgentSpec>(k: K, v: AgentSpec[K]) => setS((prev) => ({ ...prev, [k]: v }));
  const toggle = (k: "skills" | "context_docs" | "extra_tools" | "disallowed_tools", v: string) => {
    const cur = (s[k] ?? []) as string[];
    set(k, cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]);
  };
  const tiers = Object.keys(catalog.model_tiers);
  const customModel = !tiers.includes(s.model ?? "default");
  const presetTools = catalog.presets[s.tools ?? "observer"] ?? [];
  const observeOnly = catalog.observe_only_presets.includes(s.tools ?? "observer");

  return (
    <form
      className="panel editor"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(s);
      }}
    >
      <h3>{isNew ? "New agent" : `Edit agent: ${s.id}`}</h3>

      <div className="grid2">
        <label>
          Id {isNew ? "" : <span className="muted small">(ids can't be renamed; duplicate instead)</span>}
          <input
            value={s.id}
            onChange={(e) => set("id", e.target.value)}
            disabled={!isNew}
            placeholder="security-reviewer"
            pattern="[a-z][a-z0-9-]{1,40}"
            required
          />
        </label>
        <label>
          Description
          <input value={s.description ?? ""} onChange={(e) => set("description", e.target.value)} />
        </label>

        <label>
          Model
          <select
            value={customModel ? "__custom" : s.model}
            onChange={(e) => set("model", e.target.value === "__custom" ? "" : e.target.value)}
          >
            {tiers.map((t) => (
              <option key={t} value={t}>
                {t} → {catalog.model_tiers[t]}
              </option>
            ))}
            <option value="__custom">specific model…</option>
          </select>
          {customModel && (
            <input
              value={s.model}
              onChange={(e) => set("model", e.target.value)}
              placeholder="e.g. sonnet, opus, haiku or a full model id"
              required
            />
          )}
        </label>
        <label>
          Tool preset
          <select value={s.tools} onChange={(e) => set("tools", e.target.value as AgentSpec["tools"])}>
            {Object.entries(catalog.presets).map(([p, tools]) => (
              <option key={p} value={p}>
                {p} — {tools.join(", ")}
              </option>
            ))}
          </select>
          <span className="small muted">
            {observeOnly ? "Observe-only: cannot modify files; shell limited to read commands." : "Can modify files in the run worktree."}
          </span>
        </label>
      </div>

      <fieldset>
        <legend>Narrow or extend tools</legend>
        <div className="row">
          {presetTools.map((t) => (
            <label key={t} className="check">
              <input type="checkbox" checked={!(s.disallowed_tools ?? []).includes(t)} onChange={() => toggle("disallowed_tools", t)} />
              {t}
            </label>
          ))}
          {catalog.extra_tools.map((t) => (
            <label key={t} className="check">
              <input type="checkbox" checked={(s.extra_tools ?? []).includes(t)} onChange={() => toggle("extra_tools", t)} />
              {t} <span className="muted small">(extra)</span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>
          Skills{" "}
          <a href="/skills" target="_blank" rel="noreferrer" className="small">
            import from GitHub ↗
          </a>
        </legend>
        <div className="checks">
          {catalog.skills.map((sk) => (
            <label key={sk.name} className="check" title={sk.description}>
              <input type="checkbox" checked={(s.skills ?? []).includes(sk.name)} onChange={() => toggle("skills", sk.name)} />
              {sk.name} {sk.vendored && <span className="muted small">(vendored)</span>}
              {sk.source === "github" && (
                <span className="pill info" title={`${sk.repo}/${sk.path} @ ${sk.sha}`}>
                  github @{sk.sha?.slice(0, 7)}
                </span>
              )}
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>Context</legend>
        <div className="checks">
          {docs.length === 0 && <span className="muted small">No reference documents yet — add them below.</span>}
          {docs.map((d) => (
            <label key={d.id} className="check">
              <input type="checkbox" checked={(s.context_docs ?? []).includes(d.id)} onChange={() => toggle("context_docs", d.id)} />
              {d.title}
            </label>
          ))}
        </div>
        <label>
          Earlier iterations of the product to recall (0–{catalog.max_previous_iterations})
          <input
            type="number"
            min={0}
            max={catalog.max_previous_iterations}
            value={s.previous_iterations ?? 0}
            onChange={(e) => set("previous_iterations", Number(e.target.value))}
          />
        </label>
        <label>
          Learnings <span className="muted small">(human-approved lessons, max {catalog.max_learnings_chars} chars)</span>
          <textarea
            rows={3}
            maxLength={catalog.max_learnings_chars}
            value={s.learnings ?? ""}
            onChange={(e) => set("learnings", e.target.value)}
          />
        </label>
      </fieldset>

      <div className="grid2">
        <label>
          Max turns
          <input type="number" min={1} max={500} value={s.max_turns ?? 40} onChange={(e) => set("max_turns", Number(e.target.value))} />
        </label>
        <label>
          Must produce (one path per line)
          <textarea
            rows={2}
            value={(s.produces ?? []).join("\n")}
            onChange={(e) => set("produces", e.target.value.split("\n").map((x) => x.trim()).filter(Boolean))}
            placeholder="docs/security-review.md"
          />
        </label>
      </div>

      <label>
        Prompt
        <textarea className="mono" rows={14} value={s.prompt ?? ""} onChange={(e) => set("prompt", e.target.value)} required />
      </label>

      {error && <p className="error">{error}</p>}
      <div className="row">
        <button disabled={saving}>{saving ? "Saving…" : "Save to draft"}</button>
        <button type="button" className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
