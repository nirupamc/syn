"""M3 tests: management plane (/admin/*) endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(client):
    """Test client with admin secret configured (uses the shared client fixture)."""
    return client


# ---- Admin auth required ----------------------------------------------------


def test_admin_requires_secret(admin_client):
    resp = admin_client.get("/admin/users")
    assert resp.status_code == 401


def test_admin_with_wrong_secret(admin_client):
    resp = admin_client.get(
        "/admin/users", headers={"X-Admin-Secret": "wrong"}
    )
    assert resp.status_code == 401


def test_admin_with_correct_secret(admin_client):
    resp = admin_client.get(
        "/admin/users", headers={"X-Admin-Secret": "test-admin-secret"}
    )
    assert resp.status_code == 200


def test_admin_with_bearer_secret(admin_client):
    resp = admin_client.get(
        "/admin/users", headers={"Authorization": "Bearer test-admin-secret"}
    )
    assert resp.status_code == 200


# ---- Inference key cannot administer ----------------------------------------


def test_inference_key_cannot_administer(auth_env):
    """An /v1/* API key must NOT be valid for /admin/*."""
    resp = auth_env["client"].get(
        "/admin/users",
        headers={"Authorization": f"Bearer {auth_env['token']}"},
    )
    assert resp.status_code == 401


# ---- Users CRUD -------------------------------------------------------------


def test_create_user_via_admin(admin_client):
    resp = admin_client.post(
        "/admin/users",
        json={"name": "alice"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "alice"
    assert data["status"] == "active"
    assert "id" in data


def test_list_users_via_admin(admin_client):
    admin_client.post(
        "/admin/users",
        json={"name": "alice"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    admin_client.post(
        "/admin/users",
        json={"name": "bob"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    resp = admin_client.get(
        "/admin/users", headers={"X-Admin-Secret": "test-admin-secret"}
    )
    assert resp.status_code == 200
    names = {u["name"] for u in resp.json()}
    assert names == {"alice", "bob"}


def test_create_duplicate_user_returns_409(admin_client):
    admin_client.post(
        "/admin/users",
        json={"name": "alice"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    resp = admin_client.post(
        "/admin/users",
        json={"name": "alice"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    assert resp.status_code == 409


# ---- Clients CRUD -----------------------------------------------------------


def test_create_client_via_admin(admin_client):
    user_resp = admin_client.post(
        "/admin/users",
        json={"name": "alice"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    user_id = user_resp.json()["id"]

    resp = admin_client.post(
        "/admin/clients",
        json={"user_id": user_id, "name": "huginn"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "huginn"
    assert data["user_id"] == user_id
    assert data["allowed_models"] == []


def test_create_client_with_allowed_models(admin_client):
    user_resp = admin_client.post(
        "/admin/users",
        json={"name": "alice"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    user_id = user_resp.json()["id"]

    resp = admin_client.post(
        "/admin/clients",
        json={
            "user_id": user_id,
            "name": "huginn",
            "allowed_models": ["model-a", "model-b"],
        },
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    assert resp.status_code == 201
    assert sorted(resp.json()["allowed_models"]) == ["model-a", "model-b"]


def test_list_clients_via_admin(admin_client):
    user_resp = admin_client.post(
        "/admin/users",
        json={"name": "alice"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    user_id = user_resp.json()["id"]
    admin_client.post(
        "/admin/clients",
        json={"user_id": user_id, "name": "c1"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    resp = admin_client.get(
        "/admin/clients", headers={"X-Admin-Secret": "test-admin-secret"}
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ---- API key CRUD -----------------------------------------------------------


def test_create_api_key_via_admin(admin_client):
    user_resp = admin_client.post(
        "/admin/users",
        json={"name": "alice"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    user_id = user_resp.json()["id"]
    client_resp = admin_client.post(
        "/admin/clients",
        json={"user_id": user_id, "name": "c"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    client_id = client_resp.json()["id"]

    resp = admin_client.post(
        "/admin/api-keys",
        json={"client_id": client_id, "name": "dev-key"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "dev-key"
    assert "key" in data
    assert data["key"].startswith("syn_live_")
    assert "id" in data
    assert "key_prefix" in data


def test_list_api_keys_does_not_leak_secret(admin_client):
    user_resp = admin_client.post(
        "/admin/users",
        json={"name": "alice"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    user_id = user_resp.json()["id"]
    client_resp = admin_client.post(
        "/admin/clients",
        json={"user_id": user_id, "name": "c"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    client_id = client_resp.json()["id"]

    create_resp = admin_client.post(
        "/admin/api-keys",
        json={"client_id": client_id, "name": "k"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    full_key = create_resp.json()["key"]

    list_resp = admin_client.get(
        "/admin/api-keys", headers={"X-Admin-Secret": "test-admin-secret"}
    )
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 1
    item = items[0]
    # Full secret must NOT be in list response
    assert "key" not in item
    assert "key_hash" not in item
    assert full_key not in str(item)


# ---- Revocation -------------------------------------------------------------


def test_revoke_api_key_via_admin(admin_client):
    user_resp = admin_client.post(
        "/admin/users",
        json={"name": "alice"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    user_id = user_resp.json()["id"]
    client_resp = admin_client.post(
        "/admin/clients",
        json={"user_id": user_id, "name": "c"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    client_id = client_resp.json()["id"]
    create_resp = admin_client.post(
        "/admin/api-keys",
        json={"client_id": client_id, "name": "k"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    api_key_id = create_resp.json()["id"]

    resp = admin_client.post(
        f"/admin/api-keys/{api_key_id}/revoke",
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["revoked_at"] is not None


# ---- Rotation ---------------------------------------------------------------


def test_rotate_api_key_via_admin(admin_client):
    user_resp = admin_client.post(
        "/admin/users",
        json={"name": "alice"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    user_id = user_resp.json()["id"]
    client_resp = admin_client.post(
        "/admin/clients",
        json={"user_id": user_id, "name": "c"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    client_id = client_resp.json()["id"]
    create_resp = admin_client.post(
        "/admin/api-keys",
        json={"client_id": client_id, "name": "k"},
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    old_id = create_resp.json()["id"]
    old_key = create_resp.json()["key"]

    rotate_resp = admin_client.post(
        f"/admin/api-keys/{old_id}/rotate",
        headers={"X-Admin-Secret": "test-admin-secret"},
    )
    assert rotate_resp.status_code == 200
    data = rotate_resp.json()
    assert "key" in data
    assert data["key"] != old_key  # new key is different
    assert data["rotated_from"] == old_id
    assert data["name"].endswith("(rotated)")
