import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, FileUp, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, unwrap } from "../api/client";

type Parsed = Record<string, Record<string, unknown>>;

/**
 * What a change brings from the org's own tools when its lane starts after the phases that would
 * make it (ADR-0039 §5): a test report from CI, a change-risk report, an image. Shown only when the
 * product's lane for this kind of change takes any. The engine checks each against its schema when
 * the change starts.
 */
export default function ProvidedArtifacts({
  productId,
  kind,
  onChange,
}: {
  productId: string;
  kind: string;
  /** null while something is missing or not valid JSON */
  onChange: (provided: Parsed | null) => void;
}) {
  const entry = useQuery({
    queryKey: ["change-entry", productId, kind],
    queryFn: () =>
      unwrap(api.GET("/api/products/{product_id}/entry", { params: { path: { product_id: productId }, query: { kind } } })),
  });
  const takes = entry.data?.takes ?? [];
  const [text, setText] = useState<Record<string, string>>({});

  const parse = (raw: string): Record<string, unknown> | string => {
    if (!raw.trim()) return "Paste it, load a file or start from the example.";
    try {
      const v = JSON.parse(raw);
      return v && typeof v === "object" && !Array.isArray(v) ? v : "It must be a JSON object.";
    } catch (e) {
      return `Not valid JSON: ${(e as Error).message}`;
    }
  };
  const states = Object.fromEntries(takes.map((t) => [t.key, parse(text[t.key] ?? "")]));
  const ready = takes.every((t) => typeof states[t.key] === "object");
  const key = JSON.stringify(text) + takes.map((t) => t.key).join();
  useEffect(() => {
    if (!takes.length) return onChange({});
    onChange(ready ? (states as Parsed) : null);
  }, [key]);

  if (!takes.length) return null;
  return (
    <fieldset className="bg-card" aria-label="What this change brings">
      <legend>From your own tools</legend>
      <p className="m-0 text-xs text-muted-foreground">
        This product's lane starts after the phases that make these, so each change brings them. They are checked when the
        change starts and kept with its evidence as provided by you.
      </p>
      {takes.map((t) => {
        const state = states[t.key];
        const ok = typeof state === "object";
        return (
          <div key={t.key} className="grid gap-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">{t.title}</span>
              <code className="text-xs">{t.ref}</code>
              {ok && (
                <span className="pill ok">
                  <CheckCircle2 /> valid JSON
                </span>
              )}
              <span className="ml-auto flex gap-1.5">
                <button
                  type="button"
                  className="secondary small-btn"
                  onClick={() => setText({ ...text, [t.key]: JSON.stringify(t.example, null, 2) })}
                >
                  <Wand2 className="size-3.5" /> Start from the example
                </button>
                <label className="secondary small-btn m-0 inline-flex cursor-pointer flex-row items-center gap-1.5 rounded-md border bg-card px-2.5 py-1 text-xs font-medium hover:bg-muted">
                  <FileUp className="size-3.5" /> Load a file
                  <input
                    type="file"
                    accept="application/json,.json"
                    className="sr-only"
                    onChange={async (e) => {
                      const f = e.target.files?.[0];
                      if (f) setText({ ...text, [t.key]: await f.text() });
                    }}
                  />
                </label>
              </span>
            </div>
            <textarea
              aria-label={t.title}
              rows={5}
              spellCheck={false}
              value={text[t.key] ?? ""}
              onChange={(e) => setText({ ...text, [t.key]: e.target.value })}
            />
            {!ok && <span className="text-xs text-muted-foreground">{state as string}</span>}
          </div>
        );
      })}
    </fieldset>
  );
}
