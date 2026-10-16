"""Smoke tests against a running deployment. Skipped unless E2E_BASE_URL is set,
e.g. `E2E_BASE_URL=http://localhost:8081 uv run pytest tests/e2e`."""

import os

import httpx
import pytest

BASE = os.getenv("E2E_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="E2E_BASE_URL not set")


def test_health_ready_and_metrics() -> None:
    with httpx.Client(base_url=BASE or "", timeout=10) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/readyz").status_code == 200
        assert "http_requests_total" in c.get("/metrics").text
