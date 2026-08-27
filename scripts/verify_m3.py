"""M3 Runtime Verification Script.

Tests Syn's authenticated API against a real llama.cpp backend.
"""
import json
import sys
import urllib.request
import urllib.error


SYN_URL = "http://127.0.0.1:8001"
ADMIN_SECRET = "test-admin-secret"


def http_get(path, headers=None):
    url = f"{SYN_URL}{path}"
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def http_post(path, data, headers=None):
    url = f"{SYN_URL}{path}"
    body = json.dumps(data).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def admin_post(path, data):
    return http_post(path, data, headers={"X-Admin-Secret": ADMIN_SECRET})


def redact(token):
    """Show only the prefix of a token."""
    if len(token) > 17:
        return token[:17] + "...REDACTED"
    return token


def main():
    print("=" * 60)
    print("M3 Runtime Verification")
    print("=" * 60)

    # ---- A: Bootstrap credentials via admin API ----
    print("\n--- A: Bootstrap credentials ---")
    status, user = admin_post("/admin/users", {"name": "m3-test-user"})
    assert status == 201, f"create user failed: {status}"
    user_id = user["id"]
    print(f"  User created: {user_id}")

    status, client = admin_post("/admin/clients", {
        "user_id": user_id,
        "name": "m3-test-client",
    })
    assert status == 201, f"create client failed: {status}"
    client_id = client["id"]
    print(f"  Client created: {client_id}")

    status, key_out = admin_post("/admin/api-keys", {
        "client_id": client_id,
        "name": "m3-test-key",
    })
    assert status == 201, f"create key failed: {status}"
    full_token = key_out["key"]
    print(f"  API key created: {redact(full_token)}")

    # ---- B: Unauthenticated → 401 ----
    print("\n--- B: Unauthenticated GET /v1/models ---")
    status, body = http_get("/v1/models")
    print(f"  Status: {status}")
    print(f"  Body: {json.dumps(body)}")
    assert status == 401, f"expected 401, got {status}"
    assert body["error"]["code"] == "authentication_required"

    # ---- C: Invalid key → 401 ----
    print("\n--- C: Invalid key ---")
    status, body = http_get(
        "/v1/models",
        headers={"Authorization": "Bearer syn_live_aaaaaaaa_" + "x" * 43},
    )
    print(f"  Status: {status}")
    print(f"  Body: {json.dumps(body)}")
    assert status == 401
    assert body["error"]["code"] == "invalid_api_key"

    # ---- D: Valid OpenAI SDK ----
    print("\n--- D: Valid OpenAI SDK ---")
    from openai import OpenAI

    client = OpenAI(
        base_url=f"{SYN_URL}/v1",
        api_key=full_token,
    )

    # models.list()
    models = client.models.list()
    model_ids = [m.id for m in models.data]
    print(f"  models.list(): {model_ids}")
    assert len(model_ids) >= 1, "no models returned"

    model_id = model_ids[0]

    # chat.completions.create()
    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Reply with exactly: SYN_AUTH_OK"},
        ],
        temperature=0,
        max_tokens=100,
    )
    content = response.choices[0].message.content
    print(f"  chat.completions.create(): content={content!r}")
    assert content and content.strip(), "empty content from SDK"

    # ---- E: Model restriction (not tested at runtime since client is unrestricted) ----
    print("\n--- E: Model restriction ---")
    # Create a restricted client + key via admin
    user_id = users[0]["id"]
    status, restricted_client = http_post(
        "/admin/clients",
        {"user_id": user_id, "name": "restricted-test", "allowed_models": [model_id]},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert status == 201

    status, restricted_key = http_post(
        "/admin/api-keys",
        {"client_id": restricted_client["id"], "name": "restricted-key"},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert status == 201
    restricted_token = restricted_key["key"]
    print(f"  Restricted token: {redact(restricted_token)}")

    # Try a forbidden model
    forbidden = "definitely-not-a-real-model"
    status, body = http_post(
        "/v1/chat/completions",
        {"model": forbidden, "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {restricted_token}"},
    )
    print(f"  Forbidden model: {status}")
    # Should be 404 (model not found) since the model doesn't exist at all
    assert status == 404

    # Try the allowed model
    status, body = http_post(
        "/v1/chat/completions",
        {"model": model_id, "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {restricted_token}"},
    )
    print(f"  Allowed model: {status}")
    assert status == 200

    # ---- F: Revocation ----
    print("\n--- F: Revocation ---")
    # Revoke the original key
    original_key_id = keys[0]["id"]
    status, revoked = http_post(
        f"/admin/api-keys/{original_key_id}/revoke",
        {},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert status == 200
    print(f"  Revoked key at: {revoked['revoked_at']}")

    # Now the original key should fail
    status, body = http_get(
        "/v1/models", headers={"Authorization": f"Bearer {full_token}"}
    )
    print(f"  Revoked key request: {status}")
    assert status == 401, f"revoked key still works! status={status}"

    # ---- G: Rotation ----
    print("\n--- G: Rotation ---")
    # Rotate the restricted key (which is still active)
    status, rotated = http_post(
        f"/admin/api-keys/{restricted_key['id']}/rotate",
        {},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert status == 200
    new_token = rotated["key"]
    print(f"  New token: {redact(new_token)}")
    assert new_token != restricted_token

    # New key should work
    status, body = http_get(
        "/v1/models", headers={"Authorization": f"Bearer {new_token}"}
    )
    print(f"  New key request: {status}")
    assert status == 200

    print("\n" + "=" * 60)
    print("ALL CHECKS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
