"""`agent-factory` CLI: serve the API, export the OpenAPI contract, manage lines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-factory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the API + engine")
    serve.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container-internal
    serve.add_argument("--port", type=int, default=8000)
    exp = sub.add_parser("openapi", help="write the OpenAPI contract for the web UI")
    exp.add_argument("--out", default="../web/openapi.json")

    line = sub.add_parser("line", help="versioned line management")
    lsub = line.add_subparsers(dest="line_cmd", required=True)
    lsub.add_parser("versions", help="list line versions")
    le = lsub.add_parser("export", help="write a line version as line.yaml + agents/*.md")
    le.add_argument("--out", required=True)
    le.add_argument("--version", type=int, default=None, help="default: the active version")
    li = lsub.add_parser("import", help="validate a line folder and store it as a new version")
    li.add_argument("path")
    li.add_argument("--note", required=True, help="what changed, e.g. 'add security-reviewer after build'")
    li.add_argument("--no-activate", action="store_true", help="store without making it active")
    la = lsub.add_parser("activate", help="make a version active for new runs (rollback)")
    la.add_argument("version", type=int)

    args = parser.parse_args()

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("agent_factory.app_factory:app", host=args.host, port=args.port)
    elif args.cmd == "openapi":
        from agent_factory.app_factory import create_app

        spec = create_app().openapi()
        Path(args.out).write_text(json.dumps(spec, indent=2) + "\n")
        print(f"wrote {args.out}")
    elif args.cmd == "line":
        _line(args)


def _line(args: argparse.Namespace) -> None:
    from agent_factory.app_factory import build_factory
    from agent_factory.engine.lines import LineError
    from agent_factory.line import export_line_dir

    lines = build_factory().lines
    try:
        if args.line_cmd == "versions":
            for v in lines.versions():
                mark = "*" if v.active else " "
                print(f"{mark} v{v.version:<4} {v.created_at[:19]}  {v.stations} stations, {v.agents} agents  {v.note}")
        elif args.line_cmd == "export":
            version = args.version or lines.active_version()
            export_line_dir(lines.get(version), Path(args.out))
            print(f"exported line v{version} to {args.out}")
        elif args.line_cmd == "import":
            version = lines.import_dir(Path(args.path), args.note, activate=not args.no_activate)
            print(f"stored line v{version}" + ("" if args.no_activate else " (active)"))
        elif args.line_cmd == "activate":
            lines.activate(args.version)
            print(f"line v{args.version} is now active for new runs")
    except LineError as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
