from fastapi.testclient import TestClient

from app.main import app


def test_healthz() -> None:
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz() -> None:
    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 200
