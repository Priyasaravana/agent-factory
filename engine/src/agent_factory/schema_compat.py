"""Is a change's schema compatible with the version kept for an instant switch back? (ADR-0046
slice 4, safeguard decided 2026-11-01.) The pure part.

Blue/green keeps the colour that served before running on the same database, so after the switch
the old version must still work with whatever the new version did to the data. The engine compares
the schema-defining code of the release users are on with the tested commit and allows only
additive changes: new tables (and new indexes), which the old version never reads. Anything else
(an existing table's definition changed or no longer created, `alter`, `drop`, `truncate`, `rename`,
a migration that changes or removes) is not additive, and blue/green is refused for that change: a
person makes the change additive (expand now, contract in a later release) or releases it in place.

What counts as schema-defining code, in every stack the packs build: SQL in source or `.sql` files
(`create table …` in the Next.js pack), SQLAlchemy models (`__tablename__`, the FastAPI pack) and
Alembic-style operations (`op.add_column`, …). Tests are not schema. Fail closed: a statement it
can't place is treated as a change.

These functions decide; `engine/stations.py` reads both commits with git.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

# what `git grep` looks for to find candidate files (case-insensitive extended regex)
GREP = (
    r"create[[:space:]]+table|alter[[:space:]]+table|drop[[:space:]]+(table|column|index)|truncate[[:space:]]|"
    r"rename[[:space:]]+(to|column)|__tablename__|op\.(create|drop|add|alter|rename)_"
)
SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".mjs", ".cjs", ".py", ".sql", ".prisma")
_TEST_PARTS = ("tests/", "test/", "e2e/", "__tests__/", "node_modules/")

_SQL = re.compile(
    r"\b(create\s+(?:unique\s+)?(?:table|index)|alter\s+table|drop\s+(?:table|column|index)|truncate|rename\s+(?:to|column))\b",
    re.I,
)
_CREATE_TABLE = re.compile(r"^create\s+table\s+(?:if\s+not\s+exists\s+)?(?!if\b)[\"`\[]?([\w.]+)", re.I)
_CREATE_INDEX = re.compile(r"^create\s+(?:unique\s+)?index\b", re.I)
_OP = re.compile(r"\bop\.(create_table|create_index|add_column|alter_column|drop_\w+|rename_\w+)\s*\(([^)]*)", re.I)
_CLASS = re.compile(r"^class\s+\w+\s*\(.*\)\s*:\s*$")
_TABLENAME = re.compile(r"__tablename__\s*=\s*[\"']([\w.]+)[\"']")


def is_schema_source(path: str) -> bool:
    """A file that may define the schema: source in a known language, not a test. Pure."""
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if any(part in f"/{p}" for part in (f"/{t}" for t in _TEST_PARTS)) or p.startswith(_TEST_PARTS):
        return False
    if re.search(r"(^test_|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$)", name):
        return False
    return p.endswith(SOURCE_SUFFIXES)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _sql_statements(text: str) -> list[str]:
    """Each DDL statement from where its keyword starts to the end of the statement or of the string
    that holds it (`;`, a backtick or a quote), whitespace-normalised. Pure."""
    out, taken = [], 0
    for m in _SQL.finditer(text):
        if m.start() < taken:
            continue  # inside the statement just read (`alter table … drop column …`)
        rest = text[m.start() :]
        end = min((i for i in (rest.find(c) for c in ";`\"'") if i >= 0), default=len(rest))
        out.append(_norm(rest[:end]))
        taken = m.start() + end
    return out


def _models(text: str) -> dict[str, str]:
    """SQLAlchemy models: table name -> the class body, normalised (comments and blank lines
    dropped). Pure."""
    lines, out = text.splitlines(), {}
    for i, line in enumerate(lines):
        if not _CLASS.match(line):
            continue
        body = []
        for nxt in lines[i + 1 :]:
            if nxt.strip() and not nxt.startswith((" ", "\t")):
                break
            code = nxt.split("#", 1)[0].strip()
            if code:
                body.append(code)
        joined = "\n".join(body)
        if found := _TABLENAME.search(joined):
            out[found.group(1).lower()] = _norm(joined)
    return out


@dataclass
class Schema:
    """What a commit's code says about the schema."""

    tables: dict[str, str] = field(default_factory=dict)  # table -> its definition (SQL or model body)
    additive: set[str] = field(default_factory=set)  # create index statements and op.create_* calls
    other: set[str] = field(default_factory=set)  # every other statement: alter, drop, rename, truncate


def read(files: Mapping[str, str]) -> Schema:
    """The schema a commit defines, from its schema-source files (path -> content). Pure."""
    s = Schema()
    for path, text in files.items():
        if not is_schema_source(path):
            continue
        for stmt in _sql_statements(text):
            if m := _CREATE_TABLE.match(stmt):
                s.tables[m.group(1).lower()] = stmt
            elif _CREATE_INDEX.match(stmt):
                s.additive.add(stmt)
            else:
                s.other.add(stmt)
        for name, body in _models(text).items():
            s.tables[name] = body
        for m in _OP.finditer(text):
            stmt = _norm(f"op.{m.group(1)}({m.group(2)})")
            (s.additive if m.group(1).lower() in ("create_table", "create_index") else s.other).add(stmt)
    return s


def not_additive(before: Schema, after: Schema) -> list[str]:
    """Every way `after` changes the schema the version in `before` relies on, or [] when it only
    adds tables and indexes. Pure."""
    out = []
    for name, definition in sorted(before.tables.items()):
        if name not in after.tables:
            out.append(f"table {name} is no longer created (dropped or renamed)")
        elif after.tables[name] != definition:
            out.append(f"table {name}'s definition changed")
    out += [f"new schema statement: {stmt[:120]}" for stmt in sorted(after.other - before.other)]
    return out


def new_tables(before: Schema, after: Schema) -> list[str]:
    return sorted(set(after.tables) - set(before.tables))
