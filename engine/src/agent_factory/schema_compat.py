"""Is a change's schema compatible with the version kept for an instant switch back? (ADR-0046
slice 4, safeguards decided 2026-11-01 and 2026-11-02.) The pure part.

Blue/green keeps the colour that served before running on the same database, so after the switch
the old version must still work with whatever the new version did to the schema. The engine reads
every file that differs between the commit users are on and the tested commit, and gives one of
three verdicts:

- **additive**: every schema-looking line in every changed file was understood, and the only
  difference is new objects (tables, indexes, views ...) the old version never uses;
- **not additive**: an object's definition changed or it is no longer created, or a new `alter`,
  `drop`, `truncate`, `rename`, `comment`, `grant` or `revoke` appears;
- **unsure**: something in a changed file looks like schema and the checker can't read it exactly
  (a schema library it doesn't parse, SQL built at run time, a `.tsx` or `.prisma` file, text that
  mentions DDL but isn't a SQL statement, a file it can't lex). Unsure is never treated as additive:
  a person approves compatibility explicitly, for that exact pair of commits.

How it reads, so that quoting and formatting can't hide a change: Python through `ast` (string
constants, SQLAlchemy models with `__tablename__`, `Table(...)`, Alembic `op.*`); JavaScript and
TypeScript through a lexer for strings, template literals and comments; SQL through a tokenizer that
knows quoted identifiers (`"users"`, `` `users` ``, `[users]`), string literals, dollar quoting and
comments, and splits statements only on `;` outside them. Definitions are compared token by token.

Not checked: data changes (`update`, `insert`, `delete`), and schema built entirely at run time
without any SQL keyword or known schema library in the changed files.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

ADDITIVE, NOT_ADDITIVE, UNSURE = "additive", "not_additive", "unsure"

PYTHON = (".py",)
SCRIPT = (".ts", ".js", ".mjs", ".cjs", ".mts", ".cts")  # no JSX: `'` is always a string there
SQL_FILES = (".sql",)
# never schema: prose, lock files, pictures
DOCS = (".md", ".mdx", ".txt", ".rst", ".adoc", ".lock", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp")
LOCKS = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "Pipfile.lock"}
MANIFESTS = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "setup.cfg",
    "setup.py",
    "Pipfile",
}
# schema and migration libraries the checker doesn't read: adding one makes the verdict unsure
SCHEMA_LIBS = (
    "prisma", "@prisma/client", "drizzle-orm", "drizzle-kit", "typeorm", "sequelize", "knex", "kysely", "mongoose",
    "@mikro-orm/core", "objection", "node-pg-migrate", "db-migrate", "umzug", "alembic", "sqlmodel", "django",
    "peewee", "tortoise-orm", "piccolo", "yoyo-migrations", "pony",
)  # fmt: skip
_TEST_DIRS = ("tests/", "test/", "e2e/", "__tests__/", "node_modules/")
_TEST_FILE = re.compile(r"(^test_.*\.py$|_test\.py$|^conftest\.py$|\.(test|spec)\.[cm]?[jt]sx?$)")

# DDL as it would appear anywhere: a file (either version) matching this or ORM_LIKE must be understood
DDL_LIKE = re.compile(
    r"\b(create\s+(or\s+replace\s+)?(unique\s+)?(global\s+|local\s+)?(temp(orary)?\s+|unlogged\s+)?"
    r"(materialized\s+)?(table|index|view|type|sequence|extension|function|trigger|schema|domain|procedure|policy|rule)\b"
    r"|alter\s+(table|type|sequence|view|index|schema|domain|function)\b"
    r"|drop\s+(table|column|index|view|type|sequence|constraint|schema|domain|function|trigger|extension)\b"
    r"|truncate\s+(table\s+)?[\"`\[\w]|rename\s+(to|column)\b|add\s+column\b)",
    re.I,
)
# schema libraries in code (case-sensitive: these are identifiers)
ORM_LIKE = re.compile(
    r"__tablename__|\bop\.[a-z_]+\s*\(|\bmapped_column\s*\(|\bColumn\s*\(|\bTable\s*\(|\bmodels\.Model\b|\bSQLModel\b"
    r"|\b(pg|mysql|sqlite|singlestore)Table\s*\(|@Entity\b|@Column\b|@PrimaryGeneratedColumn\b|@PrimaryColumn\b"
    r"|\bsequelize\b|\bDataTypes\.|\.(create|alter|drop|rename)Table(IfNotExists)?\s*\(|\bnew\s+(mongoose\.)?Schema\s*\("
    r"|\bmongoose\.model\s*\(|\bPrismaClient\b|\bknex\b|\bKysely\b|\bdrizzle\s*\("
)

DDL_START = {"create", "alter", "drop", "truncate", "rename", "comment", "grant", "revoke"}
# statements that change data or nothing at all: not schema (data changes are out of scope)
NOT_SCHEMA = {
    "select", "insert", "update", "delete", "with", "pragma", "begin", "commit", "rollback", "savepoint", "release",
    "set", "reset", "explain", "values", "replace", "upsert", "merge", "copy", "vacuum", "analyze", "show", "listen",
    "notify", "unlisten", "lock", "start", "end", "abort", "prepare", "execute", "deallocate", "discard", "fetch",
    "close", "declare", "move", "checkpoint", "reindex", "cluster", "refresh", "use", "describe", "desc", "attach",
    "detach", "load", "table",
}  # fmt: skip
CREATE_KINDS = {
    "table", "index", "view", "type", "sequence", "extension", "function", "trigger", "schema", "domain",
    "procedure", "policy", "rule", "aggregate", "operator", "collation", "cast", "statistics", "role",
}  # fmt: skip
_CREATE_MODIFIERS = {"or", "replace", "unique", "temp", "temporary", "unlogged", "global", "local", "materialized"}
_PY_SCHEMA_LIBS = {"sqlmodel", "django", "peewee", "tortoise", "piccolo", "pony", "yoyo"}
_PY_ORM_NAMES = {
    "Column", "mapped_column", "Table", "Index", "UniqueConstraint", "CheckConstraint", "PrimaryKeyConstraint",
    "ForeignKeyConstraint", "ForeignKey",
}  # fmt: skip


# ----------------------------------------------------------------- SQL --
def sql_tokens(text: str) -> list[str] | None:
    """SQL as tokens: words lowercased (keywords and unquoted identifiers), quoted identifiers and
    string literals kept as written, comments and whitespace dropped. None: it can't be tokenized
    (an unterminated quote, comment or dollar-quoted body). Pure."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                return None
            i = j + 2
        elif c in "'\"`":
            j = i + 1
            while True:
                k = text.find(c, j)
                if k < 0:
                    return None
                if k + 1 < n and text[k + 1] == c:  # a doubled quote is the quote itself
                    j = k + 2
                    continue
                break
            out.append(text[i : k + 1])
            i = k + 1
        elif c == "[" and (m := re.match(r"\[[^\]\[]*\]", text[i:])) and m.group(0) != "[]":
            out.append(m.group(0))
            i += len(m.group(0))
        elif c == "$" and (m := re.match(r"\$([A-Za-z_]\w*)?\$", text[i:])):
            tag = m.group(0)
            k = text.find(tag, i + len(tag))
            if k < 0:
                return None
            out.append(text[i : k + len(tag)])
            i = k + len(tag)
        elif m := re.match(r"[A-Za-z_][\w$]*", text[i:]):
            out.append(m.group(0).lower())
            i += len(m.group(0))
        elif m := re.match(r"\d[\w.]*", text[i:]):
            out.append(m.group(0))
            i += len(m.group(0))
        else:
            out.append(c)
            i += 1
    return out


def _statements(tokens: list[str]) -> list[list[str]]:
    out: list[list[str]] = []
    cur: list[str] = []
    for t in tokens:
        if t == ";":
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def _ident(tok: str) -> str | None:
    """An identifier's name: unquoted ones are already lowercase; quoted ones keep their case."""
    if re.fullmatch(r"[a-z_][\w$]*", tok):
        return tok
    if len(tok) > 2 and ((tok[0] == tok[-1] and tok[0] in '"`') or (tok[0] == "[" and tok[-1] == "]")):
        return tok[1:-1]
    return None


def _name(stmt: list[str], i: int) -> tuple[str | None, int]:
    """A possibly qualified name (`a.b`) starting at token i: (name, index after it)."""
    parts = []
    while i < len(stmt) and (part := _ident(stmt[i])) is not None:
        parts.append(part)
        i += 1
        if i < len(stmt) and stmt[i] == ".":
            i += 1
            continue
        break
    return (".".join(parts) if parts else None), i


def _balanced_from(stmt: list[str], i: int) -> bool:
    depth = 0
    for t in stmt[i:]:
        depth += t == "("
        depth -= t == ")"
        if depth < 0:
            return False
    return depth == 0


@dataclass
class Parsed:
    """What one version of one file says about the schema."""

    objects: dict[tuple[str, str], set[str]] = field(default_factory=dict)  # (kind, name) -> definitions
    other: set[str] = field(default_factory=set)  # alter, drop, rename, truncate, comment, grant, revoke
    unsure: list[str] = field(default_factory=list)

    def add(self, kind: str, name: str, definition: str) -> None:
        self.objects.setdefault((kind, name), set()).add(definition)

    def merge(self, other: Parsed) -> None:
        for key, defs in other.objects.items():
            self.objects.setdefault(key, set()).update(defs)
        self.other |= other.other
        self.unsure += other.unsure


def parse_sql(text: str, where: str) -> Parsed:
    """Every statement in `text`: DDL as objects and other statements, data statements skipped.
    Anything it can't place exactly is recorded as unsure. Pure."""
    p = Parsed()
    tokens = sql_tokens(text)
    if tokens is None:
        p.unsure.append(f"{where}: SQL that can't be read (an unterminated quote, comment or $$ body)")
        return p
    for stmt in _statements(tokens):
        head, text_of = stmt[0], " ".join(stmt)
        if head in NOT_SCHEMA:
            if head != "table" and not DDL_LIKE.search(text_of):
                continue
            p.unsure.append(f"{where}: a `{head}` statement with schema in it: {text_of[:100]}")
        elif head == "create":
            i = 1
            while i < len(stmt) and stmt[i] in _CREATE_MODIFIERS:
                i += 1
            kind = stmt[i] if i < len(stmt) else ""
            if kind not in CREATE_KINDS:
                p.unsure.append(f"{where}: a `create` the check doesn't know: {text_of[:100]}")
                continue
            i += 1
            if kind == "index" and i < len(stmt) and stmt[i] == "concurrently":
                i += 1
            if stmt[i : i + 3] == ["if", "not", "exists"]:
                i += 3
            name, after = _name(stmt, i)
            if kind == "table":
                if name is None or after >= len(stmt) or stmt[after] not in ("(", "as", "partition", "of", "like"):
                    p.unsure.append(f"{where}: a `create table` without a readable name and body: {text_of[:100]}")
                    continue
                if stmt[after] == "(" and not _balanced_from(stmt, after):
                    p.unsure.append(f"{where}: `create table {name}` with unbalanced parentheses")
                    continue
            p.add(kind, name if name and (kind != "index" or stmt[after : after + 1] == ["on"]) else text_of, text_of)
        elif head in DDL_START:
            p.other.add(text_of)
        elif DDL_LIKE.search(text_of):
            p.unsure.append(f"{where}: SQL the check doesn't know with schema in it: {text_of[:100]}")
    return p


_SQL_COMMENTS = re.compile(r"^(\s+|--[^\n]*(\n|$)|/\*.*?\*/)*", re.S)


def _string(p: Parsed, s: str, where: str, dynamic: bool = False) -> None:
    """A string literal in code: a SQL statement is parsed; anything else that mentions DDL, or SQL
    with schema built from expressions (`${}`, f-strings), is unsure. Pure."""
    body = s[_SQL_COMMENTS.match(s).end() :]  # type: ignore[union-attr]
    first = re.match(r"[A-Za-z_]+", body)
    word = first.group(0).lower() if first else ""
    if dynamic:
        if DDL_LIKE.search(s):
            p.unsure.append(f"{where}: SQL with schema built from expressions (${{…}} or an f-string)")
        return
    if word in DDL_START or word in NOT_SCHEMA:
        if word in NOT_SCHEMA and not DDL_LIKE.search(s):
            return  # an ordinary query (or prose starting with such a word): no schema in it
        p.merge(parse_sql(s, where))
    elif DDL_LIKE.search(s):
        p.unsure.append(f"{where}: text that mentions schema but isn't a SQL statement: {s.strip()[:80]!r}")


# -------------------------------------------------------------- Python --
def _assigns(st: ast.stmt, name: str) -> bool:
    targets = st.targets if isinstance(st, ast.Assign) else [st.target] if isinstance(st, ast.AnnAssign) else []
    return any(isinstance(t, ast.Name) and t.id == name for t in targets)


def parse_python(text: str, where: str) -> Parsed:
    """String constants (docstrings aside), SQLAlchemy models with `__tablename__`, `Table(...)` and
    Alembic `op.*` calls; any other use of a schema construct is unsure. Pure."""
    p = Parsed()
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        p.unsure.append(f"{where}: not valid Python ({e.msg}, line {e.lineno})")
        return p
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            if isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    understood: set[int] = set()
    # `op` is Alembic's only when imported from it (`import os.path as op` is not a migration)
    alembic_op = any(
        isinstance(n, ast.ImportFrom) and n.module == "alembic" and any((a.asname or a.name) == "op" for a in n.names)
        for n in ast.walk(tree)
    )

    def keep(node: ast.AST) -> None:
        understood.update(id(n) for n in ast.walk(node))

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            table = next(
                (
                    st.value
                    for st in node.body
                    if isinstance(st, ast.Assign | ast.AnnAssign) and _assigns(st, "__tablename__")
                ),
                None,
            )
            if table is None:
                continue
            if not (isinstance(table, ast.Constant) and isinstance(table.value, str)):
                p.unsure.append(f"{where}: class {node.name} computes its __tablename__ (line {node.lineno})")
                continue
            parts = [ast.dump(b) for b in node.bases] + [ast.dump(k) for k in node.keywords]
            parts += [ast.dump(st) for st in node.body if not (isinstance(st, ast.Expr) and id(st.value) in docstrings)]
            p.add("table", table.value.lower(), "\n".join(parts))
            keep(node)
        elif isinstance(node, ast.Call) and id(node) not in understood:
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if (
                fname == "Table"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                p.add("table", node.args[0].value.lower(), ast.dump(node))
                keep(node)
            elif (
                alembic_op and isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "op"
            ):
                first = node.args[0] if node.args else None
                named = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
                if fn.attr in ("create_table", "create_index") and named:
                    p.add(fn.attr.removeprefix("create_"), named.lower(), ast.dump(node))
                    keep(node)
                elif fn.attr == "execute":
                    pass  # its SQL string is read below
                else:
                    p.other.add(ast.unparse(node))
                    keep(node)
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            _string(p, node.value, f"{where}:{line}")
        elif isinstance(node, ast.JoinedStr):
            literal = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            _string(p, literal, f"{where}:{line}", dynamic=True)
        elif id(node) in understood:
            continue
        elif isinstance(node, ast.Name | ast.Attribute):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if (
                name in _PY_ORM_NAMES
                or name == "SQLModel"
                or (isinstance(node, ast.Attribute) and name == "Model" and ast.unparse(node.value) == "models")
            ):
                p.unsure.append(f"{where}:{line}: {name} outside a model with a constant __tablename__")
    # the module names of schema libraries, as imported
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for mod in mods:
                if mod.split(".")[0] in _PY_SCHEMA_LIBS:  # alembic is read: its `op.*` calls above
                    p.unsure.append(f"{where}: imports {mod}, a schema library the check doesn't read")
    return p


# ------------------------------------------------- JavaScript / TypeScript --
_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_WORDS = {
    "return",
    "typeof",
    "case",
    "do",
    "else",
    "in",
    "of",
    "new",
    "delete",
    "void",
    "throw",
    "yield",
    "await",
}
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0"}


@dataclass
class _Lexed:
    strings: list[tuple[str, bool, int]] = field(default_factory=list)  # (text, built from ${}, line)
    code: list[str] = field(default_factory=list)
    problem: str = ""


def lex_script(text: str) -> _Lexed:
    """The strings, template literals and code of a .ts/.js file, comments dropped. Pure."""
    out = _Lexed()
    n = len(text)

    def line(i: int) -> int:
        return text.count("\n", 0, i) + 1

    def string(i: int, q: str) -> int:
        j, buf = i + 1, []
        while j < n:
            c = text[j]
            if c == "\\" and j + 1 < n:
                buf.append(_ESCAPES.get(text[j + 1], text[j + 1]))
                j += 2
            elif c == q:
                out.strings.append(("".join(buf), False, line(i)))
                return j + 1
            elif c == "\n":
                break
            else:
                buf.append(c)
                j += 1
        out.problem = out.problem or f"unterminated string at line {line(i)}"
        return n

    def template(i: int) -> int:
        j, buf, dynamic = i + 1, [], False
        while j < n:
            c = text[j]
            if c == "\\" and j + 1 < n:
                buf.append(_ESCAPES.get(text[j + 1], text[j + 1]))
                j += 2
            elif c == "`":
                out.strings.append(("".join(buf), dynamic, line(i)))
                return j + 1
            elif text.startswith("${", j):
                dynamic = True
                buf.append(" ${…} ")
                j = code(j + 2, until_brace=True)
            else:
                buf.append(c)
                j += 1
        out.problem = out.problem or f"unterminated template literal at line {line(i)}"
        return n

    def code(i: int, until_brace: bool = False) -> int:
        depth, prev_word = 0, ""
        while i < n:
            c = text[i]
            if text.startswith("//", i):
                j = text.find("\n", i)
                i = n if j < 0 else j
            elif text.startswith("/*", i):
                j = text.find("*/", i + 2)
                if j < 0:
                    out.problem = out.problem or f"unterminated comment at line {line(i)}"
                    return n
                i = j + 2
            elif c in "'\"":
                i = string(i, c)
                out.code.append(' "" ')
                prev_word = ""
            elif c == "`":
                i = template(i)
                out.code.append(' "" ')
                prev_word = ""
            elif c == "/":
                before = "".join(out.code).rstrip()[-1:] if out.code else ""
                if before == "" or before in _REGEX_AFTER or prev_word in _REGEX_WORDS:
                    m = re.match(r"/(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])+/[a-z]*", text[i:])
                    if not m:
                        out.problem = out.problem or f"unreadable regular expression at line {line(i)}"
                        return n
                    out.code.append(" /re/ ")
                    i += len(m.group(0))
                else:
                    out.code.append(c)
                    i += 1
                prev_word = ""
            elif c == "{":
                depth += 1
                out.code.append(c)
                i += 1
            elif c == "}":
                if until_brace and depth == 0:
                    return i + 1
                depth -= 1
                out.code.append(c)
                i += 1
            elif m := re.match(r"[A-Za-z_$][\w$]*", text[i:]):
                prev_word = m.group(0)
                out.code.append(prev_word)
                i += len(prev_word)
            else:
                if not c.isspace():
                    prev_word = ""
                out.code.append(c)
                i += 1
        if until_brace:
            out.problem = out.problem or "unterminated ${…} in a template literal"
        return n

    code(0)
    return out


_LIB_IMPORT = re.compile(
    r"^(@prisma/client|prisma|drizzle-orm(/.*)?|typeorm|sequelize|knex|kysely|mongoose|@mikro-orm/.*|objection)$"
)


def parse_script(text: str, where: str) -> Parsed:
    """SQL in the strings of a .ts/.js file; any schema library in its code or imports is unsure. Pure."""
    p = Parsed()
    lexed = lex_script(text)
    if lexed.problem:
        p.unsure.append(f"{where}: can't be read exactly ({lexed.problem})")
        return p
    code = "".join(lexed.code)
    if m := ORM_LIKE.search(code):
        p.unsure.append(f"{where}: uses {m.group(0).strip()}, a schema library the check doesn't read")
    for s, dynamic, line in lexed.strings:
        if _LIB_IMPORT.match(s):
            p.unsure.append(f"{where}:{line}: imports {s}, a schema library the check doesn't read")
            continue
        _string(p, s, f"{where}:{line}", dynamic=dynamic)
    return p


# --------------------------------------------------------------- files --
def is_test(path: str) -> bool:
    p = path.replace("\\", "/")
    return (
        p.startswith(_TEST_DIRS)
        or any(f"/{d}" in f"/{p}" for d in _TEST_DIRS)
        or bool(_TEST_FILE.search(p.rsplit("/", 1)[-1]))
    )


def _looks_like_schema(text: str) -> bool:
    return bool(DDL_LIKE.search(text) or ORM_LIKE.search(text))


def _libs(text: str, name: str) -> set[str]:
    """Schema libraries a dependency manifest names."""
    if name == "package.json":
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            return {"(package.json can't be read)"}
        deps = set()
        for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            deps |= set((doc.get(key) or {}) if isinstance(doc, dict) else {})
        return {lib for lib in SCHEMA_LIBS if lib in deps}
    return {
        lib
        for lib in SCHEMA_LIBS
        if re.search(rf"(^|[\s\"'\[,]){re.escape(lib)}([\s\"'=<>~!\[;,]|$)", text, re.M | re.I)
    }


def parse_file(path: str, text: str) -> Parsed:
    """One version of one file. Pure."""
    name = path.rsplit("/", 1)[-1]
    if path.endswith(PYTHON):
        return parse_python(text, path)
    if path.endswith(SCRIPT):
        return parse_script(text, path)
    if path.endswith(SQL_FILES):
        return parse_sql(text, path)
    p = Parsed()
    if path.endswith(".prisma") or _looks_like_schema(text):
        kind = ("." + name.rsplit(".", 1)[-1]) if "." in name else name
        p.unsure.append(f"{path}: looks like schema in a {kind} file, which the check doesn't read")
    return p


@dataclass
class Verdict:
    status: str  # additive | not_additive | unsure
    problems: list[str] = field(default_factory=list)  # why it isn't additive
    unsure: list[str] = field(default_factory=list)  # what it couldn't read (those files aren't compared)
    new_objects: list[str] = field(default_factory=list)  # "table notes", ...
    files: list[str] = field(default_factory=list)  # the changed files that looked like schema


def compare(changed: Mapping[str, tuple[str | None, str | None]]) -> Verdict:
    """The verdict for the files that differ between the two commits: path -> (before, after), None
    where the file doesn't exist. Unchanged files are the same in both versions and can't make a
    difference. Pure."""
    before, after, files, unsure = Parsed(), Parsed(), [], []
    for path in sorted(changed):
        old, new = changed[path]
        name = path.rsplit("/", 1)[-1]
        if name in MANIFESTS:
            added = _libs(new or "", name) - _libs(old or "", name)
            unsure += [f"{path}: adds {lib}, a schema library the check doesn't read" for lib in sorted(added)]
            if not path.endswith(PYTHON):
                continue
        if is_test(path) or path.endswith(DOCS) or name in LOCKS:
            continue
        if any(v is not None and "\0" in v for v in (old, new)):
            continue  # binary
        if not (path.endswith(SQL_FILES + (".prisma",)) or any(v and _looks_like_schema(v) for v in (old, new))):
            continue
        files.append(path)
        versions = [parse_file(path, v) if v is not None else Parsed() for v in (old, new)]
        doubts = [u for v in versions for u in v.unsure]
        if doubts:
            # a file not read exactly in either version: its objects are not compared (a part it
            # couldn't read would look dropped); it makes the verdict unsure on its own
            unsure += doubts
            continue
        before.merge(versions[0])
        after.merge(versions[1])
    problems: list[str] = []
    for key in sorted(set(before.objects) | set(after.objects)):
        kind, name = key
        for side, parsed in (("users' version", before), ("the tested version", after)):
            if len(parsed.objects.get(key, ())) > 1:
                unsure.append(f"{kind} {name} is defined more than one way in {side}")
        if key in before.objects and key not in after.objects:
            problems.append(f"{kind} {name} is no longer created (dropped or renamed)")
        elif key in before.objects and before.objects[key] != after.objects[key]:
            problems.append(f"{kind} {name}'s definition changed")
    problems += [f"new schema statement: {s[:120]}" for s in sorted(after.other - before.other)]
    new_objects = [f"{k} {n}" for k, n in sorted(set(after.objects) - set(before.objects))]
    status = NOT_ADDITIVE if problems else UNSURE if unsure else ADDITIVE
    return Verdict(status, problems, _unique(unsure), new_objects, files)


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
