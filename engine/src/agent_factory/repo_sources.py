"""Repository sources (ADR-0047): how the factory reaches one set of team repos.

A source names a host, the owners (orgs or users) it covers, its REST API, whether it may push, and
*references* to its token and webhook secret. Sources come from the config (the owner's) and from the
DB (made by admins on Integrations). Which source a repo uses is decided here, as pure functions, and
checked again at every use: a source that no longer covers a repo never hands its token to it.

Safety floor (config only): `existing_repos.allowed_hosts` (no source may reach another host) and
`existing_repos.source_token_refs` (which references an admin-made source may name).
"""

from __future__ import annotations

import fnmatch
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_factory.assess import RepoRef

DEFAULT_ID = "default"
ANY_OWNER = "*"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_REF = re.compile(r"^[a-z][a-z0-9+.-]*://[^\s#]+(#[^\s]+)?$")


class SourceError(ValueError):
    """A source is malformed, not allowed, or doesn't cover a repo. Never carries a secret value."""


AuditAction = Literal["created", "updated", "deleted", "product_moved"]


class SourceEdit(BaseModel):
    """One audit record of who changed which credentials reach which repos (ADR-0047 §4): a source made,
    changed or deleted, or a repo moved to another source. Kept in its own log, so it outlives the source.
    Field names only: never a value, and no reference (the source view shows those to admins)."""

    source_id: str
    by: str
    at: datetime
    action: AuditAction
    fields: list[str] = Field(default_factory=list)  # the fields an update changed
    detail: str = ""  # e.g. the product moved, and from which source


class RepoSource(BaseModel):
    """How the factory reaches one set of repositories (ADR-0047)."""

    id: str = Field(description="a short name: lowercase letters, digits and '-'")
    title: str = Field(default="", max_length=80)
    provider: Literal["github"] = "github"  # github.com or GitHub Enterprise
    host: str = Field(default="github.com", description="the git host, e.g. github.com or ghe.example.com")
    api_url: str = Field(
        default="", description="its REST API; default https://api.github.com or https://<host>/api/v3"
    )
    owners: list[str] = Field(default_factory=list, description="orgs or users this source covers")
    access: Literal["read", "write"] = "write"
    token_ref: str | None = Field(default=None, description="a secret reference, e.g. env://REPO_ACME_TOKEN")
    webhook_secret_ref: str | None = None
    # where it comes from: the config file (read-only in the UI) or an admin (the DB)
    origin: Literal["config", "admin", "default"] = "admin"
    created_by: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("id: 2-40 lowercase letters, digits or '-', starting with a letter or digit")
        return v

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip().lower()
        if not _HOST.match(v):
            raise ValueError("host: a DNS name such as github.com (no scheme, port or path)")
        return v

    @field_validator("owners")
    @classmethod
    def _owners(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for o in v:
            o = o.strip()
            if o != ANY_OWNER and not _OWNER.match(o):
                raise ValueError(f"owner '{o}': letters, digits, '-', '_' and '.' only")
            if o.lower() not in {x.lower() for x in out}:
                out.append(o)
        if len(out) > 50:
            raise ValueError("at most 50 owners per source")
        return out

    @field_validator("token_ref", "webhook_secret_ref")
    @classmethod
    def _reference(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        v = v.strip()
        if not _REF.match(v):
            raise ValueError("secret settings must be a reference like env://NAME, never a value")
        return v

    @model_validator(mode="after")
    def _api(self) -> RepoSource:
        if not self.api_url:
            self.api_url = default_api_url(self.host)
        api_host_ok(self.host, self.api_url)
        if not self.title:
            self.title = self.id
        return self

    def covers_any_owner(self) -> bool:
        return ANY_OWNER in self.owners

    def names_owner(self, owner: str) -> bool:
        return owner.lower() in {o.lower() for o in self.owners if o != ANY_OWNER}

    def covers(self, ref: RepoRef) -> bool:
        return ref.host == self.host and (self.covers_any_owner() or self.names_owner(ref.owner))

    def gh_repo(self, ref: RepoRef) -> str:
        """How `gh --repo` names the repo: `owner/name` on github.com, `host/owner/name` elsewhere."""
        return ref.slug if self.host == "github.com" else f"{self.host}/{ref.slug}"

    def gh_env(self, token: str) -> dict[str, str]:
        """The environment `gh` needs for this host (the token only ever travels this way)."""
        if self.host == "github.com":
            return {"GH_TOKEN": token}
        return {"GH_HOST": self.host, "GH_ENTERPRISE_TOKEN": token}


def default_api_url(host: str) -> str:
    return "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"


def api_host_ok(host: str, api_url: str) -> None:
    """The API must be https on the source's own host (or `api.<host>`): its token is sent there."""
    u = urlsplit(api_url)
    if u.scheme != "https" or u.username or u.password or u.query or u.fragment:
        raise SourceError("api_url must be a plain https URL")
    api_host = (u.hostname or "").lower()
    if api_host not in {host, f"api.{host}"}:
        raise SourceError(f"api_url must be on {host} or api.{host}, not {api_host or 'nothing'}")
    if not re.fullmatch(r"(/[A-Za-z0-9._-]+)*/?", u.path or ""):
        raise SourceError("api_url path may use letters, digits, '.', '_' and '-'")


# ------------------------------------------------------------ the floor --
def check_floor(source: RepoSource, allowed_hosts: list[str], token_patterns: list[str]) -> None:
    """What config allows. Every source must be on an allowed host; an admin-made source must name its
    owners and may only name references the config allows (`source_token_refs`)."""
    if source.host not in {h.lower() for h in allowed_hosts}:
        raise SourceError(
            f"host '{source.host}' is not allowed (existing_repos.allowed_hosts: {', '.join(allowed_hosts)})"
        )
    if source.origin != "admin":
        return
    if not source.owners:
        raise SourceError("name at least one owner (org or user) this source covers")
    if source.covers_any_owner():
        raise SourceError("'*' (any owner) is for config sources only: name the orgs or users")
    for name, ref in (("token", source.token_ref), ("webhook secret", source.webhook_secret_ref)):
        if ref and not ref_allowed(ref, token_patterns):
            allowed = ", ".join(token_patterns) or "none (existing_repos.source_token_refs is empty)"
            raise SourceError(f"the {name} reference {ref} is not one sources may use (allowed: {allowed})")


def ref_allowed(ref: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(ref, p) for p in patterns)


# ------------------------------------------------------------ resolving --
def candidates(ref: RepoRef, sources: list[RepoSource]) -> list[RepoSource]:
    """Sources covering the repo, best first: those that name its owner, then those covering any owner."""
    named = [s for s in sources if s.host == ref.host and s.names_owner(ref.owner)]
    wild = [s for s in sources if s.host == ref.host and s.covers_any_owner() and s not in named]
    return named + wild


def choose(ref: RepoRef, sources: list[RepoSource], chosen: str | None = None) -> RepoSource:
    """The source a repo uses (ADR-0047 §3)."""
    if chosen:
        src = next((s for s in sources if s.id == chosen), None)
        if src is None:
            raise SourceError(f"repository source '{chosen}' not found")
        if not src.covers(ref):
            raise SourceError(f"source '{src.id}' doesn't cover {ref.host}/{ref.slug} (it covers {scope(src)})")
        return src
    named = [s for s in sources if s.host == ref.host and s.names_owner(ref.owner)]
    if len(named) > 1:
        raise SourceError(
            f"{len(named)} sources name {ref.owner} on {ref.host} ({', '.join(s.id for s in named)}): choose one"
        )
    found = candidates(ref, sources)
    if not found:
        raise SourceError(
            f"no repository source covers {ref.host}/{ref.owner}: "
            "an admin adds one on Integrations → Repository sources"
        )
    if not named and len(found) > 1:
        raise SourceError(f"{len(found)} sources cover {ref.host} ({', '.join(s.id for s in found)}): choose one")
    return found[0]


def still_covers(ref: RepoRef, sources: list[RepoSource], recorded: str) -> RepoSource:
    """At every use: the recorded source must still exist and still cover the repo; never another one."""
    src = next((s for s in sources if s.id == recorded), None)
    if src is None:
        raise SourceError(f"the repository source '{recorded}' no longer exists: an admin moves the product to another")
    if not src.covers(ref):
        raise SourceError(f"the repository source '{recorded}' no longer covers {ref.host}/{ref.slug} ({scope(src)})")
    return src


def scope(src: RepoSource) -> str:
    owners = "any owner" if src.covers_any_owner() else ", ".join(src.owners) or "no owners"
    return f"{src.host}: {owners}"


def diff_fields(old: RepoSource, new: RepoSource) -> list[str]:
    keys = ("title", "host", "api_url", "owners", "access", "token_ref", "webhook_secret_ref")
    return [k for k in keys if getattr(old, k) != getattr(new, k)]
