"""Ops files that ship with the stack (nginx, Makefile): small contract checks."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_index_html_is_revalidated_on_every_load_but_assets_are_not_touched():
    conf = (REPO / "web" / "nginx.conf").read_text()
    m = re.search(r"location = /index\.html \{(.*?)\}", conf, re.S)
    assert m and 'add_header Cache-Control "no-cache";' in m.group(1)
    assert conf.count("Cache-Control") == 1, "hashed assets keep nginx's defaults"


def test_clean_docker_never_touches_volumes_or_the_cluster():
    mk = (REPO / "Makefile").read_text()
    body = re.search(r"^clean-docker:.*?(?=^\S)", mk, re.S | re.M).group(0)
    for part in ("builder prune", "image prune", "container prune"):
        assert part in body
    assert "--volumes" not in body and "volume prune" not in body and "system prune" not in body
    assert "image prune --all" not in body and "image prune -a" not in body, "tagged images stay"
    assert "clean-docker" in mk.splitlines()[0], ".PHONY"


# S-10 (assessment 2026-11-15): security headers on every response, limits on sign-in and the webhook


def _locations(conf: str) -> dict[str, str]:
    return {m.group(1).strip(): m.group(2) for m in re.finditer(r"location ([^{]+)\{(.*?)\n  \}", conf, re.S)}


def test_every_response_carries_the_security_headers():
    conf = (REPO / "web" / "nginx.conf").read_text()
    snippet = (REPO / "web" / "security-headers.conf").read_text()
    server = conf[conf.index("server {") :]
    assert "include /etc/nginx/security-headers.conf;" in server.split("location")[0], "server-wide"
    # nginx drops inherited add_header lines in a location that has its own: each such location
    # includes the snippet again
    for name, body in _locations(conf).items():
        if "add_header" in body:
            assert "include /etc/nginx/security-headers.conf;" in body, name
    lines = [line for line in snippet.splitlines() if line.startswith("add_header")]
    assert lines and all(line.rstrip().endswith("always;") for line in lines), "on refusals too"
    for header in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy"):
        assert f"add_header {header} " in snippet
    assert "COPY security-headers.conf /etc/nginx/security-headers.conf" in (REPO / "web" / "Dockerfile").read_text()


def test_the_csp_refuses_inline_scripts_and_framing():
    snippet = (REPO / "web" / "security-headers.conf").read_text()
    csp = re.search(r'Content-Security-Policy "([^"]+)"', snippet).group(1)
    rules = dict(part.strip().split(" ", 1) for part in csp.split(";"))
    assert rules["script-src"] == "'self'", "no inline or remote scripts"
    assert rules["frame-ancestors"] == "'none'" and rules["object-src"] == "'none'"
    assert rules["connect-src"] == "'self'"


def test_the_ui_page_has_no_inline_script():
    from html.parser import HTMLParser

    class Scripts(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.found: list[tuple[dict[str, str | None], str]] = []
            self._open: dict[str, str | None] | None = None

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "script":
                self._open = dict(attrs)
                self.found.append((self._open, ""))

        def handle_data(self, data: str) -> None:
            if self._open is not None:
                attrs, text = self.found[-1]
                self.found[-1] = (attrs, text + data)

        def handle_endtag(self, tag: str) -> None:
            if tag == "script":
                self._open = None

    parser = Scripts()
    parser.feed((REPO / "web" / "index.html").read_text())
    assert parser.found, "the page loads its scripts"
    for attrs, text in parser.found:
        assert attrs.get("src") and not text.strip(), f"inline script refused by the CSP: {attrs} {text[:60]!r}"
    assert (REPO / "web" / "public" / "theme.js").is_file()


def test_sign_in_and_the_webhook_are_rate_limited():
    conf = (REPO / "web" / "nginx.conf").read_text()
    locs = _locations(conf)
    assert "limit_req zone=signin" in locs["= /auth/login"]
    assert "$cross_site_write" in locs["= /auth/login"], "the cross-site refusal stays on sign-in"
    # ADR-0052 (PR #221 review): single sign-on's starts and callbacks share the sign-in limit per address
    assert "limit_req zone=signin" in locs["^~ /auth/sso/"]
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in locs["^~ /auth/sso/"], "auth caps per address"
    assert "limit_req zone=webhook" in locs["= /api/hooks/github"]
    assert "limit_req_status 429;" in conf
    assert re.search(r"^\s*client_max_body_size 1m;", conf.split("location")[0], re.M), "stated, not raised"
