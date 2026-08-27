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
    # The test settings point at a closed loopback port, so the backend is
    # deterministically unreachable — reachability is reported honestly.
    assert body["backend"]["reachable"] is False
    assert body["backend"]["state"] in {"unreachable", "timeout"}
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


def test_gateway_remains_alive_with_backend_offline(client):
    """Decision: liveness is 200 even when the backend is unreachable."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["backend"]["reachable"] is False


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
    body = resp.json()
    assert set(body["backend"].keys()) == {
        "configured",
        "reachable",
        "state",
        "reason",
        "server_version",
        "model",
    }


async def test_backend_is_wired_on_app_state(client):
    """The app lifespan builds a backend instance on app.state."""
    backend = client.app.state.backend
    assert backend is not None
    assert backend.name == "llama_cpp"