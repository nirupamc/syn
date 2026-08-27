"""M3 tests: authentication flow on /v1/* endpoints."""

from __future__ import annotations

import datetime as _dt

import pytest

from app.core import api_keys
from app.services import admin as admin_service


def test_missing_authorization_returns_401(auth_env):
    resp = auth_env["client"].get("/v1/models")
    assert resp.status_code == 401
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == "authentication_required"


def test_malformed_authorization_returns_401(auth_env):
    resp = auth_env["client"].get(
        "/v1/models", headers={"Authorization": "NotBearer xyz"}
    )
    assert resp.status_code == 401


def test_bearer_with_empty_token_returns_401(auth_env):
    resp = auth_env["client"].get(
        "/v1/models", headers={"Authorization": "Bearer "}
    )
    assert resp.status_code == 401


def test_invalid_token_format_returns_401(auth_env):
    resp = auth_env["client"].get(
        "/v1/models", headers={"Authorization": "Bearer not-a-real-key"}
    )
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] == "invalid_api_key"


def test_invalid_token_returns_401(auth_env):
    fake = "syn_live_aaaaaaaa_" + "x" * 43
    resp = auth_env["client"].get(
        "/v1/models", headers={"Authorization": f"Bearer {fake}"}
    )
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] == "invalid_api_key"


def test_valid_token_authenticates(auth_env):
    resp = auth_env["client"].get(
        "/v1/models",
        headers={"Authorization": f"Bearer {auth_env['token']}"},
    )
    # Backend is offline (127.0.0.1:59999), so we get 502, not 401.
    # The point is auth passed.
    assert resp.status_code != 401


def test_revoked_token_returns_401(auth_env):
    # Revoke the key
    db = auth_env["client"].app.state.database
    session = db.session_factory()
    try:
        admin_service.revoke_api_key(session, auth_env["api_key"].id)
    finally:
        session.close()

    resp = auth_env["client"].get(
        "/v1/models",
        headers={"Authorization": f"Bearer {auth_env['token']}"},
    )
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] == "invalid_api_key"  # revoked → invalid_api_key


def test_expired_token_returns_401(auth_env):
    # Expire the key by setting expires_at in the past (UTC)
    db = auth_env["client"].app.state.database
    session = db.session_factory()
    try:
        from app.models.api_key import ApiKey

        api_key = (
            session.query(ApiKey).filter(ApiKey.id == auth_env["api_key"].id).one()
        )
        api_key.expires_at = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) - _dt.timedelta(hours=1)
        session.commit()
    finally:
        session.close()

    resp = auth_env["client"].get(
        "/v1/models",
        headers={"Authorization": f"Bearer {auth_env['token']}"},
    )
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] == "expired_api_key"


def test_auth_logs_prefix_not_secret(auth_env, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="syn.auth")
    auth_env["client"].get(
        "/v1/models",
        headers={"Authorization": f"Bearer {auth_env['token']}"},
    )
    # The full token should never appear in logs
    assert auth_env["token"] not in caplog.text
    # The key hash should never appear in logs
    token_hash = api_keys.hash_api_key(auth_env["token"])
    assert token_hash not in caplog.text
