"""Egress proxy for agent sandboxes: the only way out of the sandbox network.

Standard library only, so the same file runs in the sandbox image. It speaks
plain HTTP proxying (absolute-URI requests) and CONNECT tunnels (HTTPS), and
allows a destination only if it matches the allowlist:

    api.anthropic.com:443      exact host and port
    *.pythonhosted.org:443     any subdomain (not the bare domain)
    dind:8081-8100             a port range (apps deployed to the local cluster)

A route sends an allowed destination to another address, keeping the request's
host name (HTTP Host header, TLS SNI), e.g. apps behind the local ingress:

    *.localtest.me:8180=dind:8180

Every decision is logged as one JSON line, so denied attempts are evidence.
TLS is never terminated here: the proxy sees host names, not content.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass

MAX_HEADER = 64 * 1024


@dataclass(frozen=True)
class Rule:
    host: str
    port_lo: int
    port_hi: int

    @classmethod
    def parse(cls, text: str) -> Rule:
        host, _, ports = text.strip().rpartition(":")
        if not host or not ports:
            raise ValueError(f"egress rule '{text}' must be host:port or host:lo-hi")
        lo, _, hi = ports.partition("-")
        return cls(host.lower(), int(lo), int(hi or lo))

    def matches(self, host: str, port: int) -> bool:
        if not self.port_lo <= port <= self.port_hi:
            return False
        host = host.lower().rstrip(".")
        if self.host.startswith("*."):
            return host.endswith(self.host[1:])
        return host == self.host


@dataclass(frozen=True)
class Route:
    match: Rule
    host: str
    port: int

    @classmethod
    def parse(cls, text: str) -> Route:
        pattern, eq, upstream = text.strip().partition("=")
        host, _, port = upstream.rpartition(":")
        if not eq or not host or not port.isdigit():
            raise ValueError(f"route '{text}' must be pattern:port=host:port")
        return cls(Rule.parse(pattern), host, int(port))


class Allowlist:
    def __init__(self, rules: list[str], routes: list[str] | None = None) -> None:
        self.rules = [Rule.parse(r) for r in rules if r.strip()]
        self.routes = [Route.parse(r) for r in routes or [] if r.strip()]

    def allows(self, host: str, port: int) -> bool:
        return any(r.matches(host, port) for r in self.rules)

    def upstream(self, host: str, port: int) -> tuple[str, int]:
        """Where to connect for an allowed destination (a route, or the destination itself)."""
        for r in self.routes:
            if r.match.matches(host, port):
                return r.host, r.port
        return host, port


def parse_target(method: str, target: str) -> tuple[str, int, str]:
    """(host, port, origin-form path) for a proxied request line."""
    if method == "CONNECT":
        host, _, port = target.rpartition(":")
        return host.strip("[]"), int(port), ""
    if not target.lower().startswith("http://"):
        raise ValueError("only absolute http:// URLs or CONNECT are proxied")
    rest = target[7:]
    hostport, slash, path = rest.partition("/")
    host, _, port = hostport.rpartition(":") if ":" in hostport else (hostport, "", "80")
    return host, int(port or 80), "/" + path if slash else "/"


def log(**fields: object) -> None:
    print(json.dumps({"ts": round(time.time(), 3), **fields}), flush=True)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001, S110 - already closed
            pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, allow: Allowlist) -> None:
    peer = writer.get_extra_info("peername")
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
        host, port, path = parse_target(method.upper(), target)
    except ValueError as exc:
        writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        log(decision="bad-request", peer=str(peer), error=str(exc))
        return
    if not allow.allows(host, port):
        body = f"egress to {host}:{port} is not on the sandbox allowlist\n".encode()
        writer.write(
            b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nConnection: close\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        log(decision="deny", host=host, port=port, method=method, peer=str(peer))
        return
    up_host, up_port = allow.upstream(host, port)
    try:
        up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(up_host, up_port), timeout=15)
    except (OSError, TimeoutError) as exc:
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        log(decision="error", host=host, port=port, error=str(exc))
        return
    log(
        decision="allow",
        host=host,
        port=port,
        method=method,
        **({"via": f"{up_host}:{up_port}"} if up_host != host else {}),
    )
    if method.upper() == "CONNECT":
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
    else:
        # forward one request in origin form; drop hop-by-hop proxy headers
        kept = [ln for ln in lines[1:] if ln and not ln.lower().startswith(("proxy-", "connection:"))]
        up_writer.write(
            (f"{method} {path} {version}\r\n" + "\r\n".join([*kept, "Connection: close"]) + "\r\n\r\n").encode(
                "latin-1"
            )
        )
        await up_writer.drain()
    await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def serve(listen: str, port: int, allow: Allowlist) -> None:
    server = await asyncio.start_server(lambda r, w: handle(r, w, allow), listen, port, limit=MAX_HEADER)
    log(
        decision="listening", listen=listen, port=port, rules=[f"{r.host}:{r.port_lo}-{r.port_hi}" for r in allow.rules]
    )
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Allowlist egress proxy for agent sandboxes")
    ap.add_argument("--listen", default="0.0.0.0")  # noqa: S104 - reachable only on the sandbox network
    ap.add_argument("--port", type=int, default=3128)
    ap.add_argument("--allow", action="append", default=[], help="host:port or *.domain:lo-hi (repeatable)")
    ap.add_argument("--route", action="append", default=[], help="pattern:port=host:port (repeatable)")
    args = ap.parse_args(argv)
    rules = args.allow or [r for r in os.environ.get("EGRESS_ALLOW", "").split(",") if r]
    if not rules:
        print("no egress rules: refusing to start an allow-nothing proxy by accident", file=sys.stderr)
        sys.exit(2)
    asyncio.run(serve(args.listen, args.port, Allowlist(rules, args.route)))


if __name__ == "__main__":
    main()
