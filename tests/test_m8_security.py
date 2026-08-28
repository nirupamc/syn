"""M8 security / hardening tests (local, no Cloudflare).

Covers:
* request body size limit (SYN_MAX_REQUEST_BODY_BYTES → 413)
* CORS restrictive default and explicit allow list
* admin/inference auth still required
* backend URL not exposed through public API
* error privacy (no secrets/tracebacks)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import admin as admin_service


def _settings_with(tmp_path, **overrides) -> Settings:
    db_path = tmp_path / "test.db"
    base = dict(
        app_name="Syn",
        app_version="0.1.0",
        environment="testing",
        host="127.0.0.1",
        port=8001,
        database_url=f"sqlite:///{db_path}",
        log_level="INFO",
        backend_type="llama_cpp",
        backend_base_url="http://127.0.0.1:59999",
        backend_timeout_seconds=120.0,
        backend_connect_timeout_seconds=1.0,
        backend_health_timeout_seconds=1.0,
        admin_secret="test-admin-secret",
    )
    base.update(overrides)
    # Settings validation will run; we bypass env file
    return Settings(**base)


def test_request_size_under_limit_accepted(tmp_path):
    """Body below limit must not be rejected with 413 (auth may still 401)."""
    settings = _settings_with(tmp_path, max_request_body_bytes=1_048_576)
    app = create_app(settings)
    with TestClient(app) as client:
        # Small body, no auth → expect 401 (auth required), not 413
        r = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code != 413, "small body must not be 413"
        assert r.status_code == 401

        # Small body with valid key → should reach validation/model check, not 413
        db = app.state.database
        sess = db.session_factory()
        try:
            user = admin_service.create_user(sess, "u1")
            cl = admin_service.create_client(sess, user_id=user.id, name="c1")
            _, token = admin_service.create_api_key(sess, client_id=cl.id, name="k1")
        finally:
            sess.close()
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "unknown", "messages": [{"role": "user", "content": "hi"}]},
        )
        # Could be 404 model_not_found or 502 backend, but never 413
        assert r.status_code != 413


def test_request_size_over_limit_413(tmp_path):
    """Body exceeding max_request_body_bytes → 413 request_body_too_large."""
    # Set tiny limit to make test deterministic without sending 1 MiB
    settings = _settings_with(tmp_path, max_request_body_bytes=500)
    app = create_app(settings)
    with TestClient(app) as client:
        # Craft body >500 bytes: content-length will be set by TestClient
        big_content = "x" * 2000
        r = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": big_content}]},
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 413, r.text[:500]
        j = r.json()
        # OpenAI envelope on /v1/*
        assert "error" in j
        assert j["error"]["code"] == "request_body_too_large"
        # Must not leak secrets
        assert "test-admin-secret" not in r.text
        assert "syn_live_" not in r.text

        # Even with valid auth, large body must still be 413 before auth handling
        db = app.state.database
        sess = db.session_factory()
        try:
            user = admin_service.create_user(sess, "u2")
            cl = admin_service.create_client(sess, user_id=user.id, name="c2")
            _, token = admin_service.create_api_key(sess, client_id=cl.id, name="k2")
        finally:
            sess.close()
        r2 = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "m", "messages": [{"role": "user", "content": big_content}]},
        )
        assert r2.status_code == 413
        assert r2.json()["error"]["code"] == "request_body_too_large"


def test_cors_restrictive_default(tmp_path):
    """Default SYN_CORS_ALLOWED_ORIGINS='' → no CORS headers."""
    settings = _settings_with(tmp_path, cors_allowed_origins="")
    app = create_app(settings)
    with TestClient(app) as client:
        # No Origin → no CORS header (restrictive)
        r = client.get("/health", headers={"Origin": "https://evil.com"})
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers.keys()}


def test_cors_allowed_origin(tmp_path):
    """Explicit allowed origin → CORS header echoed for that origin."""
    settings = _settings_with(tmp_path, cors_allowed_origins="https://allowed.example.com")
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.get("/health", headers={"Origin": "https://allowed.example.com"})
        # Starlette CORSMiddleware should echo allowed origin
        assert r.headers.get("access-control-allow-origin") == "https://allowed.example.com"


def test_cors_disallowed_origin(tmp_path):
    """Disallowed origin must not receive CORS allow header."""
    settings = _settings_with(tmp_path, cors_allowed_origins="https://allowed.example.com")
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.get("/health", headers={"Origin": "https://evil.com"})
        # Must NOT echo evil origin
        assert r.headers.get("access-control-allow-origin") != "https://evil.com"
        # Either absent or not evil
        assert r.headers.get("access-control-allow-origin") in (None, "https://allowed.example.com")


def test_cors_no_origin_header(tmp_path):
    """No Origin header → no CORS header."""
    settings = _settings_with(tmp_path, cors_allowed_origins="https://allowed.example.com")
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.get("/health")
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers.keys()}


def test_cors_wildcard_rejected():
    """Config must reject wildcard."""
    with pytest.raises(Exception, match=r"cors_allowed_origins"):
        Settings(cors_allowed_origins="*")
    with pytest.raises(Exception, match="wildcard"):
        Settings(cors_allowed_origins="https://a.com, *")


def test_admin_still_requires_auth(tmp_path):
    settings = _settings_with(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.get("/admin/dashboard")
        assert r.status_code == 401
        r = client.get("/admin/observability/summary")
        assert r.status_code == 401
        r = client.get("/admin/metrics")
        assert r.status_code == 401
        # inference key must not work for admin
        db = app.state.database
        sess = db.session_factory()
        try:
            user = admin_service.create_user(sess, "u3")
            cl = admin_service.create_client(sess, user_id=user.id, name="c3")
            _, token = admin_service.create_api_key(sess, client_id=cl.id, name="k3")
        finally:
            sess.close()
        r = client.get("/admin/dashboard", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


def test_inference_still_requires_auth(tmp_path):
    settings = _settings_with(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 401
        r = client.get("/v1/models")
        assert r.status_code == 401
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer syn_live_invalid_invalid_invalid_invalid"},
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 401


def test_backend_url_not_exposed(tmp_path):
    settings = _settings_with(tmp_path, backend_base_url="http://127.0.0.1:8080")
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.get("/health")
        assert "127.0.0.1:8080" not in r.text
        # backend base url must not appear via public API
        r = client.get("/v1/models", headers={"Authorization": "Bearer syn_live_aaaaaaaa_" + "x"*43})
        # Even error responses must not leak backend url
        assert "127.0.0.1:8080" not in r.text
        assert "SYN_BACKEND" not in r.text


def test_error_privacy_no_traceback(tmp_path):
    settings = _settings_with(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        # Trigger validation error
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer syn_live_aaaaaaaa_" + "x"*43},
            json={"invalid": "payload"},
        )
        assert "Traceback" not in r.text
        assert "File \"" not in r.text
        assert "test-admin-secret" not in r.text
        # Trigger admin auth error
        r = client.get("/admin/dashboard", headers={"X-Admin-Secret": "wrong"})
        assert "Traceback" not in r.text


def test_max_body_config_validation():
    with pytest.raises(ValueError):
        Settings(max_request_body_bytes=0)
    with pytest.raises(ValueError):
        Settings(max_request_body_bytes=100 * 1024 * 1024)  # >50 MiB
