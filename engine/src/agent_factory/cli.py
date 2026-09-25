"""`agent-factory` CLI: serve the API or export the OpenAPI contract."""

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
    args = parser.parse_args()

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("agent_factory.app_factory:app", host=args.host, port=args.port)
    elif args.cmd == "openapi":
        from agent_factory.app_factory import create_app

        spec = create_app().openapi()
        Path(args.out).write_text(json.dumps(spec, indent=2) + "\n")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
