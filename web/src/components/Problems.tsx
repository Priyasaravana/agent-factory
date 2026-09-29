import { AlertTriangle } from "lucide-react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";

/** An API error, with the preflight / validation problems when there are any. */
export default function Problems({ error }: { error: Error | null }) {
  if (!error) return null;
  const problems = error instanceof ApiError ? error.problems : [];
  const preflight = error.message.startsWith("preflight failed");
  return (
    <div className="problems grid gap-1.5" role="alert">
      <div className="flex items-start gap-2 font-medium">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" />
        <span>{error.message}</span>
      </div>
      {problems.length > 0 && (
        <ul className="m-0 grid gap-1 pl-6 text-foreground">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {preflight && (
        <Link to="/integrations" className="pl-6 text-sm">
          See all readiness checks →
        </Link>
      )}
    </div>
  );
}
