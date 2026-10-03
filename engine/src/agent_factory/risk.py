"""Change risk (ADR-0027): the request is screened, the diff decides.

Two pure checks, both deterministic and explainable by the rule that matched:

- `screen_request`: an order or feedback text against the acceptable-use rules
  (category 5, abuse apps). A match refuses the work before anything is built.
  The shipped rules can't be removed; operators add their own in config.
- `check_diff`: a run's change (unified diff against `main`) against rules for
  categories 1-4: weakening security, removing safety nets, destroying data,
  exfiltration or backdoors. A `hold` finding stops the run until an admin other
  than the requester approves it with a reason (break-glass on a single-admin
  install); a `note` is recorded only.

Rules over text catch common patterns, not every clever attack. The second-person
rule and the sealed evidence are the backstop; the rule set grows from real findings.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatch
from typing import Any, Literal

CATEGORIES: dict[int, str] = {
    1: "weakens security",
    2: "removes a safety net",
    3: "destroys data",
    4: "exfiltration or backdoor",
    5: "abuse app",
    6: "large removal",
}


# ------------------------------------------------------------ acceptable use --
@dataclass(frozen=True)
class AupRule:
    """A request matching `pattern` is refused, unless it also matches `unless`
    (defensive work: detecting phishing is fine, building it is not)."""

    id: str
    title: str
    pattern: str
    unless: str | None = None

    def match(self, text: str) -> re.Match[str] | None:
        m = re.search(self.pattern, text, re.I | re.S)
        if m and self.unless and re.search(self.unless, text, re.I | re.S):
            return None
        return m


_DEFENSIVE = (
    r"\b(detect\w*|classif\w*|filter\w*|block\w*|report\w*|awareness|train\w*|prevent\w*|protect\w*"
    r"|defen\w*|scan\w*|analy[sz]\w*|mitigat\w*|quarantin\w*)\b"
)

DEFAULT_AUP: tuple[AupRule, ...] = (
    AupRule(
        "aup-phishing",
        "phishing or look-alike login pages",
        r"\b(phish\w*|fake (login|sign[- ]?in)|look-?alike (site|page|login)"
        r"|clone (of )?(the )?\w+('s)? (login|sign[- ]?in))",
        _DEFENSIVE,
    ),
    AupRule(
        "aup-credential-theft",
        "stealing credentials, cookies or one-time codes",
        r"\b(steal\w*|harvest\w*|exfiltrat\w*|sniff\w*|skim\w*)\b(\W+\w+){0,4}?\W+"
        r"(passwords?|credentials|logins?|cookies|session tokens?|otps?|2fa codes?|card (numbers|details))",
    ),
    AupRule(
        "aup-malware",
        "malware: ransomware, keyloggers, trojans, botnets, cryptojacking",
        r"\b(ransomware|keylogger\w*|trojan|botnet|rootkit|cryptojack\w*|crypto-jack\w*|info-?stealer"
        r"|remote access trojan|malware)\b",
        _DEFENSIVE,
    ),
    AupRule(
        "aup-spam",
        "spam or unsolicited bulk messaging",
        r"\b(spam\w*|unsolicited (bulk )?(email|sms|messages?)|mass[- ](email|sms|dm)\w*)\b",
        _DEFENSIVE,
    ),
    AupRule(
        "aup-covert-surveillance",
        "covert surveillance or stalking of individuals",
        r"\b(stalkerware|spyware)\b|\b(track|monitor|spy on|record|locate)\w*\b(\W+\w+){0,5}?\W+(my |a |an |the )?"
        r"(wife|husband|partner|girlfriend|boyfriend|ex|spouse|person|someone|employees?|individuals?)\b"
        r"(\W+\w+){0,8}?\W+(without (their|them|her|his) (knowing|knowledge|consent)|secretly|covertly|hidden)",
        _DEFENSIVE,
    ),
    AupRule(
        "aup-dos",
        "denial-of-service tools",
        r"\b(ddos\w*|denial[- ]of[- ]service|stress[- ]?test(er)? (any|other people's|a target)|booter"
        r"|flood\w* (a |the )?(target|server|site|website))\b",
        r"\b(protect\w*|mitigat\w*|detect\w*|defen\w*)\b",
    ),
)


@dataclass(frozen=True)
class Refusal:
    rule: str
    title: str
    matched: str

    def message(self) -> str:
        return (
            f"refused by acceptable-use rule {self.rule} ({self.title}): matched “{self.matched}”. "
            "The factory doesn't build this. If you believe this is wrong, ask an admin to review the rule."
        )


def screen_request(text: str, extra: Iterable[AupRule] = ()) -> Refusal | None:
    """The first acceptable-use rule the text breaks, or None."""
    for rule in (*DEFAULT_AUP, *extra):
        m = rule.match(text)
        if m:
            return Refusal(rule.id, rule.title, " ".join(m.group(0).split())[:80])
    return None


# ------------------------------------------------------------------- diffs --
@dataclass
class FileDiff:
    path: str
    status: Literal["added", "deleted", "modified", "renamed"] = "modified"
    old_path: str | None = None
    added: list[tuple[int, str]] = field(default_factory=list)  # (new line number, text)
    removed: list[tuple[int, str]] = field(default_factory=list)  # (old line number, text)


_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff(text: str) -> list[FileDiff]:
    """Parse `git diff` output (any context size; -U0 is enough)."""
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    old_n = new_n = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            cur = FileDiff(path=m.group(2) if m else line[11:], old_path=m.group(1) if m else None)
            files.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("new file mode"):
            cur.status = "added"
        elif line.startswith("deleted file mode"):
            cur.status = "deleted"
        elif line.startswith("rename from "):
            cur.status, cur.old_path = "renamed", line[12:]
        elif line.startswith("rename to "):
            cur.path = line[10:]
        elif line.startswith(("--- ", "+++ ")):
            continue
        elif m := _HUNK.match(line):
            old_n, new_n = int(m.group(1)), int(m.group(2))
        elif line.startswith("+"):
            cur.added.append((new_n, line[1:]))
            new_n += 1
        elif line.startswith("-"):
            cur.removed.append((old_n, line[1:]))
            old_n += 1
        elif line.startswith(" "):
            old_n, new_n = old_n + 1, new_n + 1
    return files


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") or "/tests/" in path or name.startswith("test_") or name.endswith("_test.py")


def is_doc(path: str) -> bool:
    return path.startswith("docs/") or path.lower().endswith((".md", ".rst", ".txt"))


def is_code(path: str) -> bool:
    """Shipped code and config: not tests, not docs."""
    return not (is_test(path) or is_doc(path))


@dataclass(frozen=True)
class Finding:
    rule: str
    category: int
    severity: Literal["hold", "note"]
    title: str
    file: str
    line: int | None = None
    snippet: str = ""

    def as_data(self) -> dict[str, Any]:
        return {**asdict(self), "category_title": CATEGORIES[self.category]}

    def headline(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else self.file
        return f"[{self.category}·{self.rule}] {self.title} — {where}" + (f": {self.snippet}" if self.snippet else "")


@dataclass(frozen=True)
class LineRule:
    """A pattern on added (or removed) lines of files the rule applies to."""

    id: str
    category: int
    title: str
    pattern: str
    on: Literal["added", "removed"] = "added"
    files: str = "code"  # code | tests | any | a glob
    severity: Literal["hold", "note"] = "hold"

    def applies(self, path: str) -> bool:
        if self.files == "code":
            return is_code(path)
        if self.files == "tests":
            return is_test(path)
        if self.files == "any":
            return True
        return fnmatch(path, self.files) or fnmatch(path.rsplit("/", 1)[-1], self.files)


LINE_RULES: tuple[LineRule, ...] = (
    # 1 · weakening security
    LineRule("sec-cors-wildcard", 1, "CORS opened to every origin", r"allow_origins\s*=\s*\[\s*[\"']\*[\"']"),
    LineRule(
        "sec-tls-off",
        1,
        "TLS verification turned off",
        r"verify\s*=\s*False|ssl_verify\s*=\s*False|--insecure\b|InsecureSkipVerify:\s*true|rejectUnauthorized:\s*false|CERT_NONE",
    ),
    LineRule("sec-root-user", 1, "container runs as root", r"^\s*USER\s+(root|0)\b", files="*Dockerfile*"),
    LineRule(
        "sec-privileged",
        1,
        "privileged or root pod settings",
        r"privileged:\s*true|runAsNonRoot:\s*false|allowPrivilegeEscalation:\s*true|runAsUser:\s*0\b|hostNetwork:\s*true",
    ),
    LineRule("sec-debug", 1, "debug mode switched on", r"\bdebug\s*=\s*True\b|DEBUG\s*[:=]\s*[\"']?(1|true|True)\b"),
    LineRule("sec-world-writable", 1, "world-writable permissions", r"chmod\s+(-R\s+)?0?777|0o777"),
    # 2 · removing safety nets
    LineRule(
        "net-test-skipped",
        2,
        "tests skipped or expected to fail",
        r"@pytest\.mark\.(skip|xfail)\b(?!if)|pytest\.skip\(|\.only\(|\bit\.skip\(|\bxit\(",
        files="tests",
    ),
    LineRule(
        "net-ci-verify-removed",
        2,
        "CI no longer runs the verify command",
        r"make verify",
        on="removed",
        files=".github/workflows/*",
    ),
    LineRule(
        "net-suppression",
        2,
        "lint or security check suppressed",
        r"#\s*(noqa|nosec)\b|#\s*type:\s*ignore|eslint-disable|@SuppressWarnings",
        severity="note",
    ),
    # 3 · destroying data
    LineRule(
        "data-drop",
        3,
        "drops or truncates a table or column",
        r"\b(DROP\s+(TABLE|COLUMN|DATABASE|SCHEMA)|TRUNCATE(\s+TABLE)?\s+\w)|\bop\.drop_(table|column)\(|metadata\.drop_all\(",
    ),
    LineRule(
        "data-mass-delete",
        3,
        "deletes every row (no WHERE)",
        r"\bDELETE\s+FROM\s+[\w\".]+\s*(;|[\"']|$)",
    ),
    # 4 · exfiltration or backdoors
    LineRule(
        "exf-dynamic-exec",
        4,
        "runs dynamic code or a shell",
        r"\b(eval|exec)\(|pickle\.loads?\(|os\.system\(|subprocess\.\w+\([^)]*shell\s*=\s*True|yaml\.load\((?![^)]*SafeLoader)",
    ),
    LineRule(
        "exf-hidden-route",
        4,
        "debug, shell or backdoor route",
        r"@\w+\.(get|post|put|patch|delete|api_route|route)\(\s*[\"'][^\"']*(debug|backdoor|shell|exec|eval|console|__)",
    ),
    LineRule(
        "exf-hardcoded-secret",
        4,
        "credential written into the code",
        r"(?i)\b(password|passwd|secret|api[_-]?key|token|private[_-]?key)\w*\s*[:=]\s*[\"'](?!(changeme|example|test|dummy|placeholder|\$\{|<))[^\"'\s]{8,}[\"']"
        r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}",
    ),
)

_URL = re.compile(r"https?://([A-Za-z0-9.-]+)(?::\d+)?")
SAFE_HOSTS = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",  # noqa: S104 - a host name in a list, not a bind
    "example.com",
    "example.org",
    "example.net",
    "*.example",
    "*.example.com",
    "*.localtest.me",
    "*.svc",
    "*.svc.cluster.local",
    "*.local",
    "testserver",
    # specs and standards that appear in code and config as URLs, not as calls
    "json-schema.org",
    "www.w3.org",
    "schemas.xmlsoap.org",
    "opentelemetry.io",
    "github.com",
)


def _host_ok(host: str, allowed: Iterable[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == p or fnmatch(host, p) for p in (*SAFE_HOSTS, *allowed))


def _count(lines: list[tuple[int, str]], pattern: str) -> int:
    return sum(1 for _, t in lines if re.search(pattern, t))


def check_diff(files: list[FileDiff], allowed_hosts: Iterable[str] = ()) -> list[Finding]:
    """Findings for one change, most serious first. Pure function of the diff."""
    out: list[Finding] = []
    allowed = list(allowed_hosts)

    def add(rule: str, cat: int, sev: Literal["hold", "note"], title: str, f: str, ln: int | None, snip: str) -> None:
        out.append(Finding(rule, cat, sev, title, f, ln, " ".join(snip.split())[:160]))

    test_defs_removed = test_defs_added = 0
    for f in files:
        # whole files
        if f.status == "deleted":
            if is_test(f.path) and f.path.rsplit("/", 1)[-1].startswith("test_"):
                add("net-test-file-deleted", 2, "hold", "test file deleted", f.path, None, "")
            elif f.path.startswith(".github/workflows/"):
                add("net-ci-deleted", 2, "hold", "CI workflow deleted", f.path, None, "")
            elif f.path in {".pre-commit-config.yaml", ".github/dependabot.yml", ".github/CODEOWNERS", "CODEOWNERS"}:
                add("net-guard-file-deleted", 2, "hold", "repository guard deleted", f.path, None, "")
        # line rules
        for r in LINE_RULES:
            if not r.applies(f.path):
                continue
            for n, t in f.added if r.on == "added" else f.removed:
                if re.search(r.pattern, t):
                    add(r.id, r.category, r.severity, r.title, f.path, n, t)
        # removed protections (more removed than added in the same file)
        if is_code(f.path):
            auth = (
                r"Depends\(\s*(get_current_\w+|require_\w+|auth\w*|verify_\w+|current_user)"
                r"|@login_required|Security\(|HTTPBearer|OAuth2PasswordBearer"
            )
            if _count(f.removed, auth) > _count(f.added, auth):
                n, t = next((n, t) for n, t in f.removed if re.search(auth, t))
                add("sec-auth-removed", 1, "hold", "authentication check removed", f.path, n, t)
            limit = r"\b(limiter|RateLimit\w*|rate_limit\w*|slowapi)\b"
            if _count(f.removed, limit) > _count(f.added, limit):
                n, t = next((n, t) for n, t in f.removed if re.search(limit, t))
                add("sec-rate-limit-removed", 1, "hold", "rate limiting removed", f.path, n, t)
            logs = r"\b(configure_logging|JsonFormatter|audit_log\w*|logger\.(info|warning|error))\b"
            if _count(f.removed, logs) > _count(f.added, logs) + 2:
                n, t = next((n, t) for n, t in f.removed if re.search(logs, t))
                add("net-logging-removed", 2, "note", "logging or audit calls removed", f.path, n, t)
            for n, t in f.added:
                for m in _URL.finditer(t):
                    if not _host_ok(m.group(1), allowed):
                        add("exf-new-host", 4, "hold", f"calls a new outside host ({m.group(1)})", f.path, n, t)
        if f.path.endswith(("Makefile", "pyproject.toml", "setup.cfg", ".coveragerc", "package.json")):
            gate = r"(?:--cov-fail-under[= ]|fail_under\s*=\s*)(\d+)"
            old = [int(x) for _, t in f.removed for x in re.findall(gate, t)]
            new = [int(x) for _, t in f.added for x in re.findall(gate, t)]
            if old and (not new or min(new) < max(old)):
                add(
                    "net-coverage-lowered",
                    2,
                    "hold",
                    f"coverage gate lowered ({max(old)}% → {min(new) if new else 'removed'})",
                    f.path,
                    None,
                    "",
                )
        if is_test(f.path):
            test_defs_removed += _count(f.removed, r"^\s*(async\s+)?def test_")
            test_defs_added += _count(f.added, r"^\s*(async\s+)?def test_")
    if test_defs_removed > test_defs_added:
        add(
            "net-tests-removed",
            2,
            "hold",
            f"{test_defs_removed - test_defs_added} test(s) fewer than before",
            "tests/",
            None,
            "",
        )
    out.sort(key=lambda x: (x.severity != "hold", x.category, x.file, x.line or 0))
    return _dedupe(out)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, int | None]] = set()
    out = []
    for f in findings:
        key = (f.rule, f.file, f.line)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def holds(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "hold"]


def digest(findings: list[Finding]) -> str:
    """Identity of what needs approval: rules, files and lines' text, not line
    numbers (they shift when unrelated code moves). An approval covers exactly this."""
    body = json.dumps(sorted((f.rule, f.file, f.snippet) for f in holds(findings)))
    return hashlib.sha256(body.encode()).hexdigest()[:16]
