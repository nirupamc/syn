"""Request/correlation ID middleware tests."""

from __future__ import annotations


def test_assigns_request_id(client):
    resp = client.get("/health")
    rid = resp.headers.get("X-Request-ID")
    assert rid and len(rid) > 0


def test_distinct_ids_per_request(client):
    r1 = client.get("/health").headers.get("X-Request-ID")
    r2 = client.get("/health").headers.get("X-Request-ID")
    assert r1 and r2 and r1 != r2


def test_accepts_valid_upstream_id(client):
    resp = client.get("/health", headers={"X-Request-ID": "my-trace-1"})
    assert resp.headers.get("X-Request-ID") == "my-trace-1"


def test_ignores_overlong_upstream_id(client):
    """Values longer than the sanitizer limit are replaced, not trusted."""
    bad = "x" * 200
    resp = client.get("/health", headers={"X-Request-ID": bad})
    rid = resp.headers.get("X-Request-ID")
    assert rid and rid != bad and len(rid) <= 64


def test_request_id_in_response_echoes_request_env(client):
    """The header is present on the health response body too via schema."""
    resp = client.get("/health", headers={"X-Request-ID": "echo-me"})
    assert resp.json().get("request_id") == "echo-me"