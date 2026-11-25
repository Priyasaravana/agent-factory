"""S-07: the factory's images are built from locked, verified inputs. Python from the lockfiles with hashes,
every base and compose image by digest, every downloaded tool by a checksum pinned in the repo."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pin():
    spec = importlib.util.spec_from_file_location("pin_images", ROOT / "scripts" / "pin-images.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pin_images"] = mod
    spec.loader.exec_module(mod)
    return mod


P = _pin()
DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


def test_everything_the_factory_is_built_from_is_pinned():
    assert P.check(ROOT) == [], "run scripts/pin-images.py --resolve --write (needs docker buildx)"
    found = {r.tag for r in P.refs(ROOT)}
    assert {"python:3.12-slim-bookworm", "docker:27-dind", "postgres:17-alpine"} <= found


def test_python_is_installed_from_the_lockfile_with_hashes():
    for dockerfile, lock in (
        ("images/factory/Dockerfile", "engine/uv.lock"),
        ("images/auth/Dockerfile", "auth/uv.lock"),
    ):
        text = (ROOT / dockerfile).read_text()
        assert lock.split("/")[1] in text and "uv export --frozen --no-dev --no-emit-project" in text, dockerfile
        assert "--require-hashes" in text and "--no-deps ." in text, dockerfile
        installs = re.findall(r"uv pip install[^\n\\]*", text)
        assert all("--require-hashes" in i or "--no-deps" in i for i in installs), (dockerfile, installs)


def test_downloads_are_checked_against_sums_pinned_here_never_fetched_beside_them():
    text = (ROOT / "images/factory/Dockerfile").read_text()
    assert not re.search(r"curl[^\n]*\.sha256", text), "a checksum fetched with the binary proves nothing"
    for tool in ("KIND", "KUBECTL", "HELM"):
        for arch in ("AMD64", "ARM64"):
            assert re.search(rf"^ARG {tool}_SHA256_{arch}=[0-9a-f]{{64}}$", text, re.M), (tool, arch)
    assert text.count("sha256sum -c -") == 3


def test_ci_checks_the_pins_and_the_installed_versions():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    images = ci.split("\n  images:", 1)[1]
    assert "pin-images.py --check" in images and "pin-images.py --resolve" in images
    assert "--dry-run --require-hashes" in images and "Would make no changes" in images
    deps = (ROOT / ".github/dependabot.yml").read_text()
    assert "package-ecosystem: docker-compose" in deps, "compose digests are moved by Dependabot too"


# --------------------------------------------------------------- the script --
def _tree(tmp_path: Path, factory: str, compose: str) -> Path:
    for name in P.DOCKERFILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(f"FROM alpine:3@{DIGEST}\n")
    (tmp_path / P.TOOL_FILE).write_text(factory)
    (tmp_path / P.COMPOSE).write_text(compose)
    return tmp_path


FACTORY = """FROM python:3.12-slim AS base
FROM base AS app
COPY --from=base /x /x
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/
FROM scratch
ARG KUBECTL_VERSION=v1.37.2
ARG HELM_VERSION=v3.22.1
"""
COMPOSE = """services:
  a:
    image: docker:27-dind
  b:
    image: ${LITELLM_REPO:-ghcr.io/berriai/litellm:main-stable}@${LITELLM_DIGEST:-sha256:ZEROS}   # comment
  c:
    build: .
""".replace("ZEROS", "0" * 64)


def test_build_stages_and_scratch_are_not_images_and_compose_defaults_are(tmp_path):
    root = _tree(tmp_path, FACTORY, COMPOSE)
    got = [(r.file, r.ref) for r in P.refs(root) if r.file in (P.TOOL_FILE, P.COMPOSE)]
    assert got == [
        (P.TOOL_FILE, "python:3.12-slim"),
        (P.TOOL_FILE, "ghcr.io/astral-sh/uv:0.8"),
        (P.COMPOSE, "docker:27-dind"),
        (P.COMPOSE, f"ghcr.io/berriai/litellm:main-stable@sha256:{'0' * 64}"),
    ]
    problems = P.check(root)
    assert sum("not pinned" in p for p in problems) == 3, "the repo-and-digest form reads as its defaults"
    assert "images/factory/Dockerfile: no ARG KUBECTL_SHA256_AMD64" in problems


def test_write_pins_digests_keeping_the_tag_and_adds_the_sums(tmp_path):
    root = _tree(tmp_path, FACTORY, COMPOSE)
    digests = {
        "python:3.12-slim": DIGEST,
        "ghcr.io/astral-sh/uv:0.8": DIGEST,
        "docker:27-dind": DIGEST,
        "ghcr.io/berriai/litellm:main-stable": DIGEST,
    }
    sums = {f"{t}_SHA256_{a}": "c" * 64 for t in ("KUBECTL", "HELM") for a in ("AMD64", "ARM64")}
    P._write(root, digests, sums)
    assert P.check(root) == []
    factory = (root / P.TOOL_FILE).read_text()
    assert f"FROM python:3.12-slim@{DIGEST} AS base" in factory and "FROM base AS app" in factory
    assert f"ARG KUBECTL_VERSION=v1.37.2\nARG KUBECTL_SHA256_AMD64={'c' * 64}" in factory
    compose = (root / P.COMPOSE).read_text()
    litellm = f"${{LITELLM_REPO:-ghcr.io/berriai/litellm:main-stable}}@${{LITELLM_DIGEST:-{DIGEST}}}   # comment"
    assert litellm in compose, "the digest default moves; the repo default keeps its tag, never gains a digest"
    # writing again moves a pinned digest, never doubles it
    P._write(root, {"docker:27-dind": OTHER}, {"KUBECTL_SHA256_AMD64": "d" * 64})
    compose = (root / P.COMPOSE).read_text()
    assert f"docker:27-dind@{OTHER}" in compose and DIGEST not in compose.split("litellm")[0]
    assert (root / P.TOOL_FILE).read_text().count("ARG KUBECTL_SHA256_AMD64=") == 1


def test_resolve_fails_on_a_pinned_sum_that_is_not_the_published_one(tmp_path, monkeypatch, capsys):
    pinned = FACTORY.replace(
        "ARG KUBECTL_VERSION=v1.37.2", f"ARG KUBECTL_VERSION=v1.37.2\nARG KUBECTL_SHA256_AMD64={'e' * 64}"
    )
    root = _tree(tmp_path, pinned, COMPOSE)
    monkeypatch.setattr(P, "digest_of", lambda tag: DIGEST)
    monkeypatch.setattr(P, "published_sum", lambda tool, v, arch: "f" * 64)
    assert P.resolve(False, root) == 1
    out = capsys.readouterr().out
    assert "FAIL  KUBECTL v1.37.2 amd64: pinned" in out and "UNPINNED" in out


# ------------------------------------------------- overrides (review on #204) --
def _svc(image: str, build: bool = False) -> dict:
    return {"image": image, **({"build": {"context": "."}} if build else {})}


def test_what_compose_will_pull_must_be_pinned_whatever_an_override_set():
    ok = {"services": {"gw": _svc(f"ghcr.io/berriai/litellm:main-stable@{DIGEST}"), "factory": _svc("af", build=True)}}
    assert P.check_compose(ok, {}) == [], "a pinned override, and locally built images, are fine"
    mutable = {"services": {"gw": _svc("ghcr.io/berriai/litellm:main-stable")}}
    assert P.check_compose(mutable, {}) == [
        "service gw: ghcr.io/berriai/litellm:main-stable is not pinned by digest (@sha256:<64 hex>)"
    ]
    for bad in ("ghcr.io/x:main-stable@main-stable", "x@sha256:abc", f"x@sha512:{'a' * 64}", f"x@{DIGEST}0"):
        assert P.check_compose({"services": {"gw": _svc(bad)}}, {}), bad
    retired = P.check_compose(ok, {"LITELLM_IMAGE": "ghcr.io/berriai/litellm:main-stable"})
    assert len(retired) == 1 and retired[0].startswith("LITELLM_IMAGE is set"), "refused, not silently ignored"
    assert "LITELLM_REPO and LITELLM_DIGEST" in retired[0]


def test_the_shipped_compose_needs_a_digest_for_any_litellm_override():
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["model-gateway"]["image"]
    assert re.fullmatch(r"\$\{LITELLM_REPO:-[^}@]+\}@\$\{LITELLM_DIGEST:-sha256:[0-9a-f]{64}\}", compose), compose

    def resolved(env: dict[str, str]) -> str:  # compose's own interpolation of ${VAR:-default}
        return re.sub(r"\$\{(\w+):-([^}]*)\}", lambda m: env.get(m.group(1)) or m.group(2), compose)

    shipped = resolved({})
    pinned_override = resolved({"LITELLM_REPO": "ghcr.io/me/litellm:v1.81.14", "LITELLM_DIGEST": DIGEST})
    tag_only = resolved({"LITELLM_DIGEST": "main-stable"})
    repo_only = resolved({"LITELLM_REPO": "ghcr.io/me/litellm:latest"})
    assert not P.check_compose({"services": {"gw": _svc(shipped)}}, {})
    assert not P.check_compose({"services": {"gw": _svc(pinned_override)}}, {})
    assert P.check_compose({"services": {"gw": _svc(tag_only)}}, {}), "a tag in the digest's place is refused"
    assert repo_only.endswith(shipped.split("@", 1)[1]), "another repo still pulls by the pinned digest"


def test_everything_that_starts_the_stack_checks_compose_first():
    make = (ROOT / "Makefile").read_text()
    for target in ("up", "up-live", "upgrade"):
        body = make.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
        assert body.index("pin-images.py --check-compose") < body.index("docker compose up"), target
    restore = (ROOT / "scripts/restore.sh").read_text()
    assert restore.index("--check-compose") < restore.index("docker compose stop"), "before anything is touched"
    ci = (ROOT / ".github/workflows/ci.yml").read_text().split("\n  e2e:", 1)[1]
    assert ci.index("--check-compose") < ci.index("docker compose up -d --build")
    assert "LITELLM_DIGEST=main-stable docker compose --profile gateway pull" in ci, "compose itself refuses a tag"
    for name in ("docker-compose.yml", ".env.example", "Makefile", "scripts/restore.sh"):
        assert "LITELLM_IMAGE" not in (ROOT / name).read_text(), name
