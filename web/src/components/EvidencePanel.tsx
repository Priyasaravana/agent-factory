import { useQuery } from "@tanstack/react-query";
import { Download, FileCheck2, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import { LABEL } from "./StatusPill";

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** The change's sealed evidence (ADR-0023): every file with its SHA-256, re-checked on
 * every view, and a zip for the product's creator or an admin. */
export default function EvidencePanel({
  changeId,
  runStatus,
}: {
  changeId: string;
  runStatus: string;
}) {
  const q = useQuery({
    queryKey: ["evidence", changeId, runStatus],
    queryFn: () =>
      unwrap(
        api.GET("/api/changes/{change_id}/evidence", {
          params: { path: { change_id: changeId } },
        }),
      ),
  });
  const [busy, setBusy] = useState(false);
  const e = q.data;
  if (!e?.sealed) return null;
  const files = e.files ?? [];
  const drift = [
    ...(e.changed ?? []).map((p) => `changed: ${p}`),
    ...(e.missing ?? []).map((p) => `missing: ${p}`),
    ...(e.added ?? []).map((p) => `added after the seal: ${p}`),
  ];

  async function download() {
    setBusy(true);
    try {
      const r = await fetch(`/api/changes/${changeId}/evidence/bundle`);
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? `download failed (${r.status})`);
      }
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `evidence-${changeId}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="rounded-xl border bg-card p-4" data-testid="evidence">
      <summary className="flex cursor-pointer flex-wrap items-center gap-2">
        <FileCheck2 className="size-4 text-primary" />
        <span className="font-semibold">Evidence</span>
        <span className="pill muted">
          {files.length} files · {size(e.total_bytes)}
        </span>
        {e.intact ? (
          <span
            className="pill ok"
            title="every file matches the sealed manifest"
          >
            intact
          </span>
        ) : (
          <span className="pill bad" title="something changed after the seal">
            <ShieldAlert className="size-3" /> changed since sealed
          </span>
        )}
      </summary>
      <div className="mt-3 grid gap-3 text-sm">
        <p className="muted small m-0">
          Sealed {e.sealed_at ? new Date(e.sealed_at).toLocaleString() : ""}{" "}
          when the change was{" "}
          <strong>{LABEL[e.status_at_seal ?? ""] ?? e.status_at_seal}</strong>
          {e.commit && (
            <>
              {" "}
              at commit <code>{e.commit.slice(0, 12)}</code>
            </>
          )}
          . Manifest SHA-256 <code className="break-all">{e.sha256}</code>.
          Check a download with <code>sha256sum -c SHA256SUMS</code>.
        </p>
        {!e.manifest_ok && (
          <p className="error m-0">
            manifest.json no longer matches the digest recorded when it was
            sealed.
          </p>
        )}
        {drift.length > 0 && (
          <ul className="error m-0 pl-5">
            {drift.map((d) => (
              <li key={d} className="font-mono text-xs">
                {d}
              </li>
            ))}
          </ul>
        )}
        <div>
          <button className="tab" onClick={download} disabled={busy}>
            <Download className="inline size-3" />{" "}
            {busy ? "preparing…" : "Download bundle (.zip)"}
          </button>{" "}
          <span className="muted small">
            for the product's creator or an admin (it includes transcripts)
          </span>
        </div>
        <table className="wide">
          <thead>
            <tr>
              <td>file</td>
              <td>size</td>
              <td>sha256</td>
            </tr>
          </thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.path}>
                <td className="font-mono text-xs">{f.path}</td>
                <td className="tabular-nums">{size(f.bytes)}</td>
                <td className="font-mono text-xs" title={f.sha256}>
                  {f.sha256.slice(0, 16)}…
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
