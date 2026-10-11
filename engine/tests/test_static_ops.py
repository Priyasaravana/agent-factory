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
