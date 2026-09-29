"""`agent-factory` CLI: serve the API, export the OpenAPI contract, manage workflows."""

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

    wf = sub.add_parser("workflow", help="versioned workflow management (one workflow per product line)")
    wf.add_argument("--id", default=None, help="workflow (= product line) id; default: the first product line")
    wsub = wf.add_subparsers(dest="wf_cmd", required=True)
    wsub.add_parser("list", help="list workflows")
    wsub.add_parser("templates", help="list built-in workflow templates")
    wsub.add_parser("versions", help="list versions of a workflow")
    we = wsub.add_parser("export", help="write a version as workflow.yaml + agents/*.md + docs/*.md")
    we.add_argument("--out", required=True)
    we.add_argument("--version", type=int, default=None, help="default: the active version")
    wi = wsub.add_parser("import", help="validate a workflow folder and store it as a new version")
    wi.add_argument("path")
    wi.add_argument("--note", required=True, help="what changed, e.g. 'add security-review after build'")
    wi.add_argument("--no-activate", action="store_true", help="store without making it active")
    wa = wsub.add_parser("activate", help="make a version active for new runs (rollback)")
    wa.add_argument("version", type=int)

    sk = sub.add_parser("skills", help="skills library")
    ssub = sk.add_subparsers(dest="sk_cmd", required=True)
    sc = ssub.add_parser("cache", help="fetch the config's default_skills at their pinned commits (image build)")
    sc.add_argument("--out", required=True)

    args = parser.parse_args()

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("agent_factory.app_factory:app", host=args.host, port=args.port)
    elif args.cmd == "openapi":
        from agent_factory.app_factory import create_app

        spec = create_app().openapi()
        Path(args.out).write_text(json.dumps(spec, indent=2) + "\n")
        print(f"wrote {args.out}")
    elif args.cmd == "workflow":
        _workflow(args)
    elif args.cmd == "skills":
        _skills_cache(Path(args.out))


def _skills_cache(out: Path) -> None:
    """Copy each default skill folder to <out>/<repo>/<sha>/<path>, so a new
    factory can install them without GitHub access."""
    import shutil

    from agent_factory.config import load_config
    from agent_factory.github import fetch_dir
    from agent_factory.secret_refs import SecretResolver
    from agent_factory.settings import Settings
    from agent_factory.skills import seed_dir

    settings = Settings()
    cfg = load_config(settings.factory_config)
    for src in cfg.default_skills:
        for path in src.paths:
            dest = seed_dir(out, src.repo, src.sha, path)
            if (dest / "SKILL.md").exists():
                continue
            fetched = fetch_dir(
                src.repo, path, src.sha, SecretResolver(settings).resolve_optional(cfg.skills_github_token_ref)
            )
            try:
                shutil.copytree(fetched.root, dest, ignore=shutil.ignore_patterns(".git"))
            finally:
                fetched.cleanup()
            print(f"cached {src.repo}/{path}@{src.sha[:7]}")
        if src.license:
            notice = out / src.repo / src.sha / "NOTICE"
            notice.write_text(
                f"Skills from https://github.com/{src.repo} at {src.sha}, {src.license} licensed.\n"
                f"License: https://github.com/{src.repo}/blob/{src.sha}/LICENSE\n"
            )


def _workflow(args: argparse.Namespace) -> None:
    from agent_factory.app_factory import build_factory
    from agent_factory.engine.workflows import WorkflowError
    from agent_factory.workflow import export_workflow_dir

    registry = build_factory().workflows
    try:
        if args.wf_cmd == "list":
            for w in registry:
                print(f"{w.workflow_id:<24} v{w.active_version():<4} {w.get().description}")
            return
        if args.wf_cmd == "templates":
            for name, doc in registry.templates():
                print(f"{name:<28} {len(doc.stations)} stations  {doc.description}")
            return
        w = registry[args.id or registry.ids()[0]]
        if args.wf_cmd == "versions":
            for v in w.versions():
                mark = "*" if v.active else " "
                print(f"{mark} v{v.version:<4} {v.created_at[:19]}  {v.stations} stations, {v.agents} agents  {v.note}")
        elif args.wf_cmd == "export":
            version = args.version or w.active_version()
            export_workflow_dir(w.get(version), Path(args.out))
            print(f"exported {w.workflow_id} v{version} to {args.out}")
        elif args.wf_cmd == "import":
            version = w.import_dir(Path(args.path), args.note, activate=not args.no_activate)
            print(f"stored {w.workflow_id} v{version}" + ("" if args.no_activate else " (active)"))
        elif args.wf_cmd == "activate":
            w.activate(args.version)
            print(f"{w.workflow_id} v{args.version} is now active for new runs")
    except WorkflowError as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
