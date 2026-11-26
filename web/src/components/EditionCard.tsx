import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, KeyRound, PackageOpen } from "lucide-react";
import { api, unwrap } from "../api/client";
import { Badge } from "./ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "./ui/card";

// ADR-0051: which edition runs, what its licence grants and which extensions loaded. Read-only: the key is a
// mounted file (.secrets/agent_factory_licence), never entered in the UI.
const STATUS_TONE: Record<string, "ok" | "warn" | "bad"> = {
  loaded: "ok",
  refused: "warn",
  failed: "bad",
};

type Licence = {
  valid?: boolean;
  problem?: string;
  licensee?: string;
  features?: string[];
  expires?: string;
  in_grace?: boolean;
  expires_soon?: boolean;
};

export function EditionCard() {
  const q = useQuery({
    queryKey: ["edition"],
    queryFn: () => unwrap(api.GET("/api/edition")),
  });
  if (!q.data) return null;
  const { edition } = q.data;
  const extensions = q.data.extensions ?? [];
  const lic = (q.data.licence ?? null) as Licence | null;
  const enterprise = edition === "enterprise";

  return (
    <Card data-testid="edition-card">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            {enterprise ? (
              <BadgeCheck className="size-5 text-primary" />
            ) : (
              <PackageOpen className="size-5" />
            )}
            Edition: {enterprise ? "Enterprise" : "Core"}
          </CardTitle>
          <CardDescription>
            {enterprise
              ? "Core plus the licensed features. A feature loads only when the licence key covers it; everything else, safety included, is core and always on."
              : "The Apache-2.0 core: the whole journey, sandbox, policy floor and signed evidence. SSO, SIEM export, customer KMS signing and the compliance pack come with the Enterprise edition."}
          </CardDescription>
        </div>
      </CardHeader>
      {enterprise && (
        <CardContent className="grid gap-4">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <KeyRound className="size-4 text-muted-foreground" />
            {lic?.valid ? (
              <>
                <Badge
                  tone={lic.in_grace ? "bad" : lic.expires_soon ? "warn" : "ok"}
                >
                  {lic.in_grace
                    ? "expired, in grace"
                    : lic.expires_soon
                      ? "expires soon"
                      : "licensed"}
                </Badge>
                <span>
                  <strong>{lic.licensee}</strong> · until {lic.expires} ·{" "}
                  {(lic.features ?? []).join(", ")}
                </span>
              </>
            ) : (
              <>
                <Badge tone="warn">no valid licence</Badge>
                <span className="text-muted-foreground">
                  {lic?.problem ?? "no licence key"}. Put the key in{" "}
                  <code>.secrets/agent_factory_licence</code> and restart the
                  factory.
                </span>
              </>
            )}
          </div>
          {lic?.valid && lic.problem && (
            <p className="m-0 text-sm text-warn">{lic.problem}</p>
          )}
          {extensions.length > 0 && (
            <table className="wide">
              <thead>
                <tr>
                  <td>feature</td>
                  <td>status</td>
                  <td>provides</td>
                  <td>reason</td>
                </tr>
              </thead>
              <tbody>
                {extensions.map((e) => (
                  <tr
                    key={`${e.name}-${e.feature}`}
                    data-testid={`extension-${e.feature}`}
                  >
                    <td>
                      <strong>{e.feature}</strong>
                      <div className="small muted">{e.name}</div>
                    </td>
                    <td>
                      <Badge tone={STATUS_TONE[e.status] ?? "muted"}>
                        {e.status}
                      </Badge>
                    </td>
                    <td>
                      {(e.points ?? []).join(", ") || (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>{e.reason || <span className="muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      )}
    </Card>
  );
}
