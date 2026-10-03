"""What a blueprint can build, and what a product asks for (intake hardening).

A blueprint declares its `stack` (config). Intake reports the implementation
technologies the product explicitly *requires*, with the words that require them;
the engine decides (`conflicts`, a pure function) whether any is outside the
blueprint's stack. A conflict pauses the change with a question instead of
silently building something else. A deterministic keyword scan (`mentions`) is a
second opinion: a mention intake did not report is recorded, never blocking.
"""

from __future__ import annotations

import re

# canonical name -> the words that name it (lowercase; matched on word boundaries)
ALIASES: dict[str, list[str]] = {
    "python": ["python"],
    "fastapi": ["fastapi"],
    "django": ["django"],
    "flask": ["flask"],
    "node.js": ["node.js", "nodejs", "node js", "express.js", "expressjs", "nestjs", "next.js", "nextjs"],
    "typescript": ["typescript"],
    "javascript": ["javascript"],
    "go": ["golang", "go lang", "written in go", "in go,"],
    "java": ["java", "spring boot", "springboot", "kotlin"],
    "rust": ["rust"],
    ".net": [".net", "c#", "asp.net", "dotnet"],
    "ruby": ["ruby", "rails"],
    "php": ["php", "laravel"],
    "elixir": ["elixir", "phoenix framework"],
    "postgres": ["postgres", "postgresql"],
    "mysql": ["mysql", "mariadb"],
    "mongodb": ["mongodb", "mongo"],
    "redis": ["redis"],
    "dynamodb": ["dynamodb"],
    "sqlite": ["sqlite"],
    "react": ["react", "react.js", "reactjs"],
    "vue": ["vue", "vue.js", "vuejs"],
    "angular": ["angular"],
    "svelte": ["svelte", "sveltekit"],
    "graphql": ["graphql"],
    "grpc": ["grpc"],
}
_CANON = {alias: canon for canon, aliases in ALIASES.items() for alias in [canon, *aliases]}


def normalize(name: str) -> str:
    n = name.strip().lower()
    return _CANON.get(n, n)


def _pattern(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w.]){re.escape(alias)}(?![\w])")


# only the listed words are searched ("go" alone is far too common to mean Golang)
_PATTERNS = [(canon, _pattern(a)) for canon, aliases in ALIASES.items() for a in aliases]


def mentions(text: str) -> set[str]:
    """Known technologies named anywhere in the text (canonical names). Pure."""
    t = text.lower()
    return {canon for canon, p in _PATTERNS if p.search(t)}


def conflicts(required: list[str], stack: list[str]) -> list[str]:
    """Required technologies the blueprint does not build (canonical, in order). Pure.
    An empty `stack` means the blueprint declares none: nothing conflicts."""
    if not stack:
        return []
    have = {normalize(s) for s in stack}
    out: list[str] = []
    for r in required:
        n = normalize(r)
        if n and n not in have and n not in out:
            out.append(n)
    return out


def question(conflicting: list[str], quotes: dict[str, str], stack: list[str], line: str) -> str:
    asked = "; ".join(f'{c} ("{quotes[c]}")' if quotes.get(c) else c for c in conflicting)
    return (
        f"This request requires {asked}, but the '{line}' blueprint builds with {', '.join(stack)}. "
        f"Reply 'build it with {stack[0]}' to go ahead on this stack (the requirement will be recorded as "
        "changed), or cancel this change and create the product on a blueprint that supports it."
    )
