"""Health endpoint tests."""

from __future__ import annotations


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "syn"
    assert body["version"] == "0.1.0"
    assert body["environment"] == "testing"
    # Backend reachability is NOT claimed in M0.
    assert body["backend"]["reachable"] is False
    assert "X-Request-ID" in resp.headers


def test_health_liveness_alias(client):
    resp = client.get("/health/liveness")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_ready(client):
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"
    # Readiness does not claim backend readiness (M1).
    assert body["backend"]["reachable"] is False


def test_health_response_is_typed(client):
    """Responses must serialize with the typed schema (no extra/unknown keys)."""
    resp = client.get("/health")
    assert set(resp.json().keys()) == {
        "status",
        "service",
        "version",
        "environment",
        "backend",
        "request_id",
    }