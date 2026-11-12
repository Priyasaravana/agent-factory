"""The repository sources a factory knows (ADR-0047): the config's (read-only) and the ones admins made
(the DB). The rules are pure, in `repo_sources.py`; this module reads and keeps the sources, records which
source a product uses, and resolves a source's secrets for one step.

Secrets are references: a value is resolved just in time, registered with the redactor by the resolver,
returned to the caller for that step only, and never stored, logged or shown.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agent_factory import repo_sources as rs
from agent_factory.assess import RepoRef, RepoUrlError, parse_repo_url
from agent_factory.models import EventKind, Product, ProductTarget, RepoSourceInput
from agent_factory.secret_refs import SecretError

if TYPE_CHECKING:
    from agent_factory.config import FactoryConfig
    from agent_factory.secret_refs import SecretResolver
    from agent_factory.state.base import StateStore


_GENERATED_DEFAULT = re.compile(r"^default(-\d+)?$")


def _stale_generated_default(recorded: str, ref: RepoRef, sources: list[rs.RepoSource]) -> bool:
    """Whether `recorded` is a positional default id from an earlier build that no longer reaches the repo:
    `default-N` that no source has now, or `default` held by the default source of another host. Never a
    source someone made (config and admin ids can't take these while the default source is on, and a
    source that exists and is not a default is left to `still_covers`)."""
    if not _GENERATED_DEFAULT.match(recorded) or not any(s.origin == "default" for s in sources):
        return False  # default sources off: nothing generated to rebind to, `still_covers` says why
    now = next((s for s in sources if s.id == recorded), None)
    if now is None:
        return True
    return now.origin == "default" and now.host != ref.host


class RepoSources:
    def __init__(self, cfg: FactoryConfig, store: StateStore, secrets: SecretResolver) -> None:
        self.cfg, self.store, self.secrets = cfg, store, secrets

    # ------------------------------------------------------------ reading --
    @property
    def _floor(self) -> tuple[list[str], list[str]]:
        er = self.cfg.existing_repos
        return er.allowed_hosts, er.source_token_refs

    def all(self) -> list[rs.RepoSource]:
        """Config sources first (they win an id), then admin-made ones that still pass the floor: a host
        or reference the config no longer allows makes that source unusable, never silently allowed."""
        out = self.cfg.existing_repos.config_sources()
        taken = {s.id for s in out}
        hosts, refs = self._floor
        for s in self.store.list_repo_sources():
            if s.id in taken:
                continue
            try:
                rs.check_floor(s, hosts, refs)
            except rs.SourceError:
                continue
            out.append(s)
        return out

    def get(self, source_id: str) -> rs.RepoSource | None:
        return next((s for s in self.all() if s.id == source_id), None)

    def parse(self, url: str) -> RepoRef:
        try:
            return parse_repo_url(url, self.cfg.existing_repos.allowed_hosts)
        except RepoUrlError as exc:
            raise rs.SourceError(str(exc)) from exc

    def for_url(self, url: str, chosen: str | None = None) -> tuple[RepoRef, rs.RepoSource]:
        """Onboarding: the repo and the source it will use (§3)."""
        ref = self.parse(url)
        return ref, rs.choose(ref, self.all(), chosen)

    def for_product(self, product: Product, change_id: str | None = None) -> tuple[RepoRef, rs.RepoSource]:
        """Every use: the product's recorded source must still cover it (never another one). A product
        from before ADR-0047 gets one chosen now, recorded on it, with an event on the change."""
        ref = self.parse(product.repo_url or "")
        if product.repo_source:
            sources = self.all()
            if _stale_generated_default(product.repo_source, ref, sources):
                # an id an earlier build gave a default source by list position: `default` for the first
                # allowed host whatever `api_url` said, `default-N` for the others. Rebind it to the
                # default source of the repo's own host, never to a source someone made
                defaults = [s for s in sources if s.origin == "default"]
                src = rs.choose(ref, defaults)
                before, product.repo_source = product.repo_source, src.id
                self.store.save_product(product)
                if change_id:
                    self.store.add_event(
                        change_id,
                        EventKind.decision,
                        f"repository source '{before}' is now '{src.id}' (the default for {ref.host})",
                        data={"repo_source": {"from": before, "to": src.id, "chosen": "renamed default"}},
                    )
                return ref, src
            return ref, rs.still_covers(ref, sources, product.repo_source)
        src = rs.choose(ref, self.all())
        product.repo_source = src.id
        self.store.save_product(product)
        if change_id:
            self.store.add_event(
                change_id,
                EventKind.decision,
                f"repository source '{src.id}' chosen for {ref.host}/{ref.slug} ({rs.scope(src)})",
                data={"repo_source": {"id": src.id, "chosen": "first use"}},
            )
        return ref, src

    def resolve(self, ref: str | None) -> str | None:
        """A secret for one step; None when there is no reference or it doesn't resolve."""
        if not ref:
            return None
        try:
            return self.secrets.resolve(ref) or None
        except SecretError:
            return None

    def ready(self, ref: str | None) -> bool | None:
        return None if not ref else self.resolve(ref) is not None

    def products_using(self, source_id: str) -> list[Product]:
        return [
            p
            for p in self.store.list_products()
            if p.target == ProductTarget.repo and not p.archived_at and p.repo_source == source_id
        ]

    # ------------------------------------------------------------ editing --
    def _audit(
        self, source_id: str, by: str, action: rs.AuditAction, fields: list[str] | None = None, detail: str = ""
    ) -> None:
        edit = rs.SourceEdit(
            source_id=source_id, by=by, at=datetime.now(UTC), action=action, fields=fields or [], detail=detail
        )
        self.store.add_repo_source_audit(edit)

    def history(self, source_id: str | None = None, limit: int = 200) -> list[rs.SourceEdit]:
        """The audit log, newest first: one source's, or every source's (deleted ones too)."""
        return self.store.list_repo_source_audit(source_id, limit)

    def _built(self, body: RepoSourceInput, by: str) -> rs.RepoSource:
        try:
            src = rs.RepoSource(
                id=body.id,
                title=body.title,
                host=body.host,
                api_url=body.api_url,
                owners=body.owners,
                access=body.access,
                token_ref=body.token_ref,
                webhook_secret_ref=body.webhook_secret_ref,
                origin="admin",
                created_by=by,
            )
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            raise rs.SourceError(_first_error(exc)) from exc
        rs.check_floor(src, *self._floor)
        return src

    def create(self, body: RepoSourceInput, by: str) -> rs.RepoSource:
        if self.get(body.id) or self.store.get_repo_source(body.id):
            raise rs.SourceError(f"a repository source '{body.id}' already exists")
        src = self._built(body, by)
        self.store.save_repo_source(src)
        self._audit(src.id, by, "created", detail=rs.scope(src))
        return src

    def update(self, source_id: str, body: RepoSourceInput, by: str) -> rs.RepoSource:
        old = self.store.get_repo_source(source_id)
        if old is None:
            raise rs.SourceError(_not_editable(self.get(source_id), source_id))
        if body.id != source_id:
            raise rs.SourceError("a source's id can't change: make a new source and move the products to it")
        new = self._built(body, old.created_by or by)
        fields = rs.diff_fields(old, new)
        if not fields:
            return old
        moved = [p.title for p in self.products_using(source_id) if not new.covers(self.parse(p.repo_url or ""))]
        if moved:
            raise rs.SourceError(
                f"the change would stop covering {', '.join(moved[:5])}: move them to another source first"
            )
        self.store.save_repo_source(new)
        self._audit(source_id, by, "updated", fields)
        return new

    def delete(self, source_id: str, by: str) -> None:
        old = self.store.get_repo_source(source_id)
        if old is None:
            raise rs.SourceError(_not_editable(self.get(source_id), source_id))
        using = self.products_using(source_id)
        if using:
            names = ", ".join(p.title for p in using[:5])
            raise rs.SourceError(f"'{source_id}' is used by {names}: archive them or move them to another source first")
        self.store.delete_repo_source(source_id)
        self._audit(source_id, by, "deleted", detail=f"{old.title} ({rs.scope(old)})")

    def move(self, product: Product, source_id: str, by: str) -> rs.RepoSource:
        """Point a repo product at another source (admin): it must cover the repo."""
        ref = self.parse(product.repo_url or "")
        src = rs.choose(ref, self.all(), source_id)
        before = product.repo_source
        product.repo_source = src.id
        self.store.save_product(product)
        if before != src.id:
            self._audit(src.id, by, "product_moved", detail=f"{product.title} from '{before or 'none'}'")
        return src


def _not_editable(src: rs.RepoSource | None, source_id: str) -> str:
    if src is None:
        return f"repository source '{source_id}' not found"
    return f"'{source_id}' comes from the config file: change it there (existing_repos)"


def _first_error(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            first = errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()) if p != "__root__")
            msg = str(first.get("msg", exc)).removeprefix("Value error, ")
            return f"{where}: {msg}" if where else msg
        except (IndexError, TypeError):
            pass
    return str(exc)
