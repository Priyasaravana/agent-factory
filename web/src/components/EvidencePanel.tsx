import { useQuery } from "@tanstack/react-query";
import { Copy, Download, FileCheck2, ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import { LABEL } from "./StatusPill";

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

import { Panel } from "./ui/panel";

/** The change's sealed evidence (ADR-0023): every file with its SHA-256, re-checked on
 * every view, signed with the factory's evidence key (ADR-0050), and a zip for the product's
 * creator or an admin that anyone can check with scripts/verify-evidence.py. */

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
  const key = useQuery({
    queryKey: ["evidence-key"],
    queryFn: () => unwrap(api.GET("/api/evidence/key")),
    enabled: !!q.data?.signed,
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
    <Panel
      id="evidence"
      data-testid="evidence"
      bodyClassName="text-sm"
      icon={<FileCheck2 className="text-primary" />}
      title="Evidence"
      summary={
        <>
          <span className="pill muted">
            {files.length} files · {size(e.total_bytes)}
          </span>
          {e.intact ? (
            <span className="pill ok" title="every file matches the sealed manifest">
              intact
            </span>
          ) : (
            <span className="pill bad" title="something changed after the seal">
              <ShieldAlert className="size-3" /> changed since sealed
            </span>
          )}
          {e.signed ? (
            e.signature_ok ? (
              <span
                className="pill ok"
                title={`signed by the factory's evidence key ${e.signed_by ?? ""}`}
              >
                <ShieldCheck className="size-3" /> signed
              </span>
            ) : (
              <span className="pill bad" title={e.signature_problem ?? ""}>
                <ShieldAlert className="size-3" /> signature fails
              </span>
            )
          ) : (
            <span
              className="pill muted"
              title="sealed before evidence was signed: checked by its hashes only"
            >
              not signed
            </span>
          )}
        </>
      }
    >
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
        </p>
        {e.signed && !e.signature_ok && (
          <p className="error m-0">
            The signature no longer holds: {e.signature_problem}
          </p>
        )}
        {e.signed && (
          <div className="rounded-md border border-border p-3" data-testid="evidence-verify">
            <p className="m-0">
              <strong>Check it anywhere.</strong>{" "}
              <span className="muted">
                Signed by the factory's evidence key{" "}
                <code className="break-all">{e.signed_by}</code>. Publish that
                fingerprint where the people who check bundles can find it; a
                downloaded bundle then verifies away from the factory:
              </span>
            </p>
            <pre className="m-0 mt-2 overflow-x-auto text-xs">
              {`scripts/verify-evidence.py evidence-${changeId}.zip --fingerprint ${e.signed_by}`}
            </pre>
            {key.data?.allowed_signers && (
              <p className="muted small m-0 mt-2">
                Or with OpenSSH alone: put{" "}
                <button
                  className="tab"
                  title="copy the allowed_signers line"
                  onClick={() => {
                    void navigator.clipboard.writeText(key.data?.allowed_signers ?? "");
                    toast.success("allowed_signers line copied");
                  }}
                >
                  <Copy className="inline size-3" /> this line
                </button>{" "}
                in <code>allowed_signers</code>, then{" "}
                <code>
                  ssh-keygen -Y verify -f allowed_signers -I agent-factory-evidence
                  -n agent-factory-evidence -s manifest.json.sig &lt; manifest.json
                </code>{" "}
                and <code>sha256sum -c SHA256SUMS</code>.
              </p>
            )}
          </div>
        )}
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
    </Panel>
  );
}
