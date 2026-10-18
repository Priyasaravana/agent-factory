"""`agent-factory` CLI: serve the API, export the OpenAPI contract, manage workflows."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import sys
from pathlib import Path

RUNTIME_USER = "factory"


def drop_root(user: str = RUNTIME_USER) -> list[str] | None:
    """`docker compose exec factory agent-factory …` runs as root: the image starts
    as root so its entrypoint can fix ownership, and only the main process drops to
    `factory`. Anything the CLI then writes under /data (worktrees, exports, the
    sandbox probe dir) would be root-owned and unwritable for the server and the
    sandbox (uid 10001). Re-exec as the runtime user. Returns the argv it would
    exec (None when nothing to do); outside the image there is no such user."""
    if os.geteuid() != 0 or os.environ.get("AGENT_FACTORY_ALLOW_ROOT") == "1":
        return None
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return None
    setpriv = shutil.which("setpriv")
    if not setpriv:
        return None
    return [setpriv, f"--reuid={pw.pw_uid}", f"--regid={pw.pw_gid}", "--init-groups", sys.argv[0], *sys.argv[1:]]


def run() -> None:
    """Console-script entry point: drop root first, then run the CLI. (main() stays
    side-effect free so tests and callers can use it in-process.)"""
    argv = drop_root()
    if argv:
        pw = pwd.getpwnam(RUNTIME_USER)
        env = {**os.environ, "HOME": pw.pw_dir, "USER": RUNTIME_USER, "LOGNAME": RUNTIME_USER}
        os.execve(argv[0], argv, env)  # noqa: S606 - fixed argv: setpriv + our own argv
    main()


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-factory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the API + engine")
    serve.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container-internal
    serve.add_argument("--port", type=int, default=8000)
    exp = sub.add_parser("openapi", help="write the OpenAPI contract for the web UI")
    exp.add_argument("--out", default="../web/openapi.json")

    wf = sub.add_parser("workflow", help="versioned workflow management (one workflow per blueprint)")
    wf.add_argument("--id", default=None, help="workflow (= blueprint) id; default: the first blueprint")
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
    wa = wsub.add_parser("activate", help="make a version active for new changes (rollback)")
    wa.add_argument("version", type=int)

    sk = sub.add_parser("skills", help="skills library")
    ssub = sk.add_subparsers(dest="sk_cmd", required=True)
    sc = ssub.add_parser("cache", help="fetch the config's default_skills at their pinned commits (image build)")
    sc.add_argument("--out", required=True)

    rs = sub.add_parser(
        "reset", help="fresh start (ADR-0037): drop products, changes and workflows; keep members, skills, config"
    )
    rs.add_argument("--confirm", action="store_true", help="really delete (without it: show what would go)")
    sb = sub.add_parser("sandbox", help="agent sandbox (ADR-0014)")
    sbsub = sb.add_subparsers(dest="sb_cmd", required=True)
    sbsub.add_parser("check", help="prepare the sandbox and prove its isolation from inside a real one")
    sctx = sbsub.add_parser(
        "context",
        help="assemble the sandbox image build context and its `build-args` file (CI builds and tests it)",
    )
    sctx.add_argument("--out", required=True)

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
    elif args.cmd == "reset":
        _reset(args.confirm)
    elif args.cmd == "skills":
        _skills_cache(Path(args.out))
    elif args.cmd == "sandbox" and args.sb_cmd == "context":
        _sandbox_context(Path(args.out))
    elif args.cmd == "sandbox":
        _sandbox_check()


def _reset(confirm: bool) -> None:
    from agent_factory import reset
    from agent_factory.config import load_config
    from agent_factory.settings import Settings
    from agent_factory.state import SqliteStateStore

    s = Settings()
    data_dir = Path(s.data_dir or load_config(s.factory_config).factory.data_dir)
    store = SqliteStateStore(data_dir / "factory.db")
    what = reset.plan(store, data_dir) if not confirm else reset.reset(store, data_dir)
    for name, n in what.items():
        print(f"  {n:>6}  {name}")
    if confirm:
        print("reset: the next start seeds the standard workflow")
    else:
        print("nothing deleted: run again with --confirm to delete the above")


def _sandbox_context(out: Path) -> None:
    """The build context the engine builds the sandbox from, plus `<out>/build-args`: the
    `docker build` arguments for the layers the installed packs ask for (ADR-0041 §4), so CI
    builds and tests the same image the engine would."""
    from agent_factory.app_factory import sandbox_layers
    from agent_factory.config import load_config
    from agent_factory.packs import install
    from agent_factory.sandbox import toolchain
    from agent_factory.sandbox.manager import assemble_context
    from agent_factory.settings import Settings

    s = Settings()
    home = Path(s.factory_home)
    problem = assemble_context(home, out)
    if problem:
        raise SystemExit(problem)
    config = Path(s.factory_config)
    cfg = load_config(config if config.is_file() else home / ".agent-factory" / "config.yaml")
    args = toolchain.build_args(sandbox_layers(install(cfg, home)), toolchain.from_home(home))
    (out / "build-args").write_text(" ".join(args) + "\n")
    print(f"sandbox build context in {out}; build args: {' '.join(args) or '(none)'}")


def _sandbox_check() -> None:
    import asyncio
    import sys

    from agent_factory.app_factory import effective_sandbox, sandbox_layers
    from agent_factory.config import load_config
    from agent_factory.packs import install
    from agent_factory.sandbox import SandboxManager
    from agent_factory.settings import Settings

    s = Settings()
    cfg = load_config(s.factory_config)
    # the same egress as the engine's (gateway included), or ensure() would rebuild the proxy without it
    sb = effective_sandbox(cfg)
    # and the same image layers, or the check would build (and test) a different image
    layers = sandbox_layers(install(cfg, Path(s.factory_home)))
    mgr = SandboxManager(sb, Path(s.factory_home), Path(s.data_dir or cfg.factory.data_dir), layers=layers)
    print(f"sandbox image {mgr.image}; network {sb.network}; egress {sb.egress}; model via {sb.model_endpoint}")
    results = asyncio.run(mgr.probe())
    for r in results:
        print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['check']}" + (f"  ({r['detail']})" if r.get("detail") else ""))
    failed = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


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
            print(f"{w.workflow_id} v{args.version} is now active for new changes")
    except WorkflowError as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    run()
