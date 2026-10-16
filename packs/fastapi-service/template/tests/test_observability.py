import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app
from app.observability import JsonFormatter, request_id, setup_tracing


def test_metrics_count_requests_per_route() -> None:
    with TestClient(app) as client:
        client.get("/healthz")
        body = client.get("/metrics").text
    assert 'http_requests_total{method="GET",route="/healthz",status="200"}' in body


def test_request_id_is_echoed_or_generated() -> None:
    with TestClient(app) as client:
        assert (
            client.get("/healthz", headers={"x-request-id": "abc"}).headers["x-request-id"] == "abc"
        )
        assert len(client.get("/healthz").headers["x-request-id"]) == 32


def test_logs_are_json_with_the_request_id() -> None:
    token = request_id.set("rid-1")
    try:
        record = logging.LogRecord("app", logging.INFO, __file__, 1, "hello %s", ("x",), None)
        record.fields = {"route": "/items"}
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id.reset(token)
    assert line["msg"] == "hello x" and line["request_id"] == "rid-1" and line["route"] == "/items"


def test_tracing_is_off_unless_an_endpoint_is_configured(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert setup_tracing(FastAPI(), "t") is False
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    assert setup_tracing(FastAPI(), "t") is True
