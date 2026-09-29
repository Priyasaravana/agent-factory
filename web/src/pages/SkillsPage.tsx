import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type SkillInfo, type SkillPreview } from "../api/client";
import { useIsAdmin } from "../auth";

type Source = { repo: string; path: string; ref: string };

export default function SkillsPage() {
  const isAdmin = useIsAdmin();
  const qc = useQueryClient();
  const skills = useQuery({ queryKey: ["skills"], queryFn: () => unwrap(api.GET("/api/skills")) });
  const [src, setSrc] = useState<Source>({ repo: "", path: "", ref: "main" });
  const [preview, setPreview] = useState<SkillPreview | null>(null);
  const [accept, setAccept] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const refresh = () => ["skills", "catalog", "draft"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const show = (p: SkillPreview) => {
    setPreview(p);
    setAccept(false);
  };
  const doPreview = useMutation({
    mutationFn: (s: Source) => unwrap(api.POST("/api/skills/preview", { body: s })),
    onSuccess: show,
  });
  const checkUpdate = useMutation({
    mutationFn: (name: string) =>
      unwrap(api.POST("/api/skills/{name}/check-update", { params: { path: { name } } })),
    onSuccess: show,
  });
  const install = useMutation({
    mutationFn: (p: SkillPreview) =>
      unwrap(
        api.POST("/api/skills/install", {
          body: { repo: p.repo, path: p.path, ref: p.ref, sha: p.sha, accept_scripts: accept },
        }),
      ),
    onSuccess: () => {
      setPreview(null);
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (name: string) => unwrap(api.DELETE("/api/skills/{name}", { params: { path: { name } } })),
    onSuccess: refresh,
  });
  const err = doPreview.error ?? checkUpdate.error ?? install.error ?? remove.error;

  const builtin = skills.data?.filter((s) => s.source === "builtin") ?? [];
  const imported = skills.data?.filter((s) => s.source === "github") ?? [];

  return (
    <div className="stack">
      <section className="card">
        <h2>Skills</h2>
        <p className="muted">
          Skills are instructions (and optional scripts) that agents load when relevant. Pick them per agent in the
          workflow editor. Imported skills are pinned to a commit; a workflow picks up a newer commit only when it is
          published again.
        </p>
        {!isAdmin && <p className="note">Only admins can import, update or remove skills.</p>}
        <h3>Import from GitHub</h3>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            doPreview.mutate(src);
          }}
        >
          <input
            aria-label="skill repo"
            placeholder="owner/repo"
            value={src.repo}
            onChange={(e) => setSrc({ ...src, repo: e.target.value })}
          />
          <input
            aria-label="skill path"
            placeholder="path/to/skill (folder with SKILL.md)"
            value={src.path}
            onChange={(e) => setSrc({ ...src, path: e.target.value })}
            style={{ flex: 1 }}
          />
          <input
            aria-label="skill ref"
            placeholder="branch, tag or commit"
            value={src.ref}
            onChange={(e) => setSrc({ ...src, ref: e.target.value })}
          />
          <button disabled={!src.repo || doPreview.isPending}>{doPreview.isPending ? "Fetching…" : "Review"}</button>
        </form>
        <p className="muted small">
          Public repos work as-is. Private repos use a read-only <code>SKILLS_GITHUB_TOKEN</code> from{" "}
          <code>.env</code>; it is never stored or shown.
        </p>
        {err && <p className="error">{err.message}</p>}
      </section>

      {preview && (
        <PreviewCard
          p={preview}
          accept={accept}
          setAccept={setAccept}
          busy={install.isPending}
          onInstall={() => install.mutate(preview)}
          onClose={() => setPreview(null)}
        />
      )}

      <section className="card">
        <h3>Imported ({imported.length})</h3>
        {imported.length === 0 && <p className="muted small">None yet.</p>}
        <ul className="list">
          {imported.map((s) => (
            <li key={s.name} data-testid={`skill-${s.name}`}>
              <div style={{ flex: 1, minWidth: 240 }}>
                <strong>{s.name}</strong>{" "}
                <span className="pill info" title={s.sha ?? ""}>
                  @{s.sha?.slice(0, 7)}
                </span>{" "}
                {(s.scripts ?? []).length > 0 && <span className="pill warn">scripts</span>}
                <div className="small muted">{s.description}</div>
                <div className="small">
                  <code>
                    {s.repo}/{s.path}
                  </code>{" "}
                  follows <code>{s.ref}</code>
                </div>
              </div>
              <button className="secondary" onClick={() => setOpen(open === s.name ? null : s.name)}>
                Files
              </button>
              <button className="secondary" disabled={checkUpdate.isPending} onClick={() => checkUpdate.mutate(s.name)}>
                Check for update
              </button>
              <button
                className="secondary"
                disabled={remove.isPending}
                onClick={() => {
                  if (window.confirm(`Remove '${s.name}' from the picker? Published workflow versions keep their pinned copy.`))
                    remove.mutate(s.name);
                }}
              >
                Remove
              </button>
              {open === s.name && <SkillFiles name={s.name} />}
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h3>Built in ({builtin.length})</h3>
        <ul className="list">
          {builtin.map((s: SkillInfo) => (
            <li key={s.name}>
              <strong>{s.name}</strong>
              <span className="small muted">{s.description}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function PreviewCard({
  p,
  accept,
  setAccept,
  busy,
  onInstall,
  onClose,
}: {
  p: SkillPreview;
  accept: boolean;
  setAccept: (v: boolean) => void;
  busy: boolean;
  onInstall: () => void;
  onClose: () => void;
}) {
  const blocked = p.problems.length > 0 || p.up_to_date || (p.scripts.length > 0 && !accept);
  const skillMd = p.files.find((f) => f.path === "SKILL.md");
  return (
    <section className="card preview" aria-label="Skill review">
      <div className="row spread">
        <h3>
          Review <code>{p.name}</code> <span className="pill info">@{p.sha.slice(0, 7)}</span>
        </h3>
        <button className="secondary" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="small muted">
        {p.repo}/{p.path} · {p.ref} → <code>{p.sha}</code>
        {p.installed_sha && (
          <>
            {" "}
            · installed: <code>{p.installed_sha.slice(0, 7)}</code>
          </>
        )}
      </p>
      <p>{p.description}</p>
      {p.up_to_date && (
        <p className="note">
          {p.installed_sha === p.sha
            ? "Already installed at this commit."
            : `No changes to this skill since the installed commit (${p.installed_sha?.slice(0, 7)}).`}
        </p>
      )}
      {p.problems.length > 0 && (
        <div className="problems">
          <strong>Cannot install:</strong>
          <ul>{p.problems.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      )}
      {p.diff && (
        <details open>
          <summary>Changes since the installed commit</summary>
          <pre className="pre diff">
            {p.diff.split("\n").map((l, i) => (
              <span key={i} className={l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}>
                {l + "\n"}
              </span>
            ))}
          </pre>
        </details>
      )}
      {skillMd && (
        <details open={!p.diff}>
          <summary>SKILL.md</summary>
          <pre className="pre">{skillMd.content}</pre>
        </details>
      )}
      {p.files
        .filter((f) => f.path !== "SKILL.md")
        .map((f) => (
          <details key={f.path}>
            <summary>
              {f.path} <span className="small muted">({f.size} bytes)</span>{" "}
              {f.script && <span className="pill warn">script</span>}
            </summary>
            <pre className="pre">{f.content}</pre>
          </details>
        ))}
      {p.scripts.length > 0 && (
        <label className="check">
          <input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />
          I reviewed the scripts ({p.scripts.join(", ")}). Agents can run them only with their own tools, and the
          guardrails still apply.
        </label>
      )}
      <div className="row">
        <button disabled={blocked || busy} onClick={onInstall}>
          {p.installed_sha ? `Update to ${p.sha.slice(0, 7)}` : "Install"}
        </button>
      </div>
    </section>
  );
}

function SkillFiles({ name }: { name: string }) {
  const q = useQuery({
    queryKey: ["skill", name],
    queryFn: () => unwrap(api.GET("/api/skills/{name}", { params: { path: { name } } })),
  });
  if (!q.data) return <p className="muted small">Loading…</p>;
  return (
    <div style={{ width: "100%" }}>
      <p className="small muted">
        Used by: {q.data.used_by.length ? q.data.used_by.join(", ") : "no workflow"} · installed commits:{" "}
        {q.data.versions.map((v) => (
          <code key={v.sha} title={v.installed_at}>
            {v.sha.slice(0, 7)}
            {v.current ? " (current) " : " "}
          </code>
        ))}
      </p>
      {Object.entries(q.data.files).map(([path, content]) => (
        <details key={path}>
          <summary>{path}</summary>
          <pre className="pre">{content}</pre>
        </details>
      ))}
    </div>
  );
}
