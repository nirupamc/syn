"""Application creation tests."""

from __future__ import annotations


def test_app_metadata(app):
    assert app.title == "Syn"
    assert app.version == "0.1.0"
    assert app.state.settings.environment.value == "testing"


def test_health_routes_serve(client):
    for path in ("/health", "/health/liveness", "/health/ready"):
        assert client.get(path).status_code == 200


def test_v1_chat_completions_not_implemented(client):
    """OpenAI data-plane endpoints are now implemented in M2 (non-streaming)."""
    # /v1/models is implemented and requires a real backend (returns 502 when offline)
    # /v1/chat/completions is implemented as POST; GET returns 405
    assert client.get("/v1/chat/completions").status_code == 405


def test_root_route(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "syn"


def test_unknown_route_404(client):
    resp = client.get("/does/not/exist")
    assert resp.status_code == 404