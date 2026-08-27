"""M6 Runtime Verification Script.

Tests Syn's usage tracking, rate limiting, and quotas with a real
llama.cpp backend.
"""
import json
import sys
import time
import urllib.request
import urllib.error


SYN_URL = "http://127.0.0.1:8001"
ADMIN_SECRET = "test-admin-secret"
MODEL = "models\\gpt-oss-20b-Q4_K_M.gguf"


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


def admin_get(path):
    return http_get(path, headers={"X-Admin-Secret": ADMIN_SECRET})


def admin_post(path, data):
    return http_post(path, data, headers={"X-Admin-Secret": ADMIN_SECRET})


def admin_put(path, data):
    url = f"{SYN_URL}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="PUT",
        headers={"Content-Type": "application/json", "X-Admin-Secret": ADMIN_SECRET},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def redact(token):
    if len(token) > 17:
        return token[:17] + "...REDACTED"
    return token


def main():
    print("=" * 60)
    print("M6 Runtime Verification")
    print("=" * 60)

    # ---- Bootstrap credentials ----
    print("\n--- Bootstrap credentials ---")
    s, user = admin_post("/admin/users", {"name": "m6-test-user"})
    assert s == 201, f"create user failed: {s}"
    user_id = user["id"]
    s, client_obj = admin_post("/admin/clients", {"user_id": user_id, "name": "m6-test-client"})
    assert s == 201, f"create client failed: {s}"
    client_id = client_obj["id"]
    s, key_out = admin_post("/admin/api-keys", {"client_id": client_id, "name": "m6-test-key"})
    assert s == 201, f"create key failed: {s}"
    api_key = key_out["key"]
    print(f"  API key created: {redact(api_key)}")

    # ---- A: Base usage (non-streaming) ----
    print("\n--- A: Base non-streaming usage ---")
    s, body = http_post(
        "/v1/chat/completions",
        {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Reply with exactly: SYN_OK"},
            ],
            "temperature": 0,
            "max_tokens": 50,
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert s == 200, f"Expected 200, got {s}: {body}"
    usage = body.get("usage", {})
    print(f"  Response usage: {usage}")
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    total_tokens = usage.get("total_tokens", 0)
    assert prompt_tokens > 0, "prompt_tokens should be > 0"
    assert completion_tokens > 0, "completion_tokens should be > 0"
    assert total_tokens == prompt_tokens + completion_tokens

    # Check admin usage
    s, usage_summary = admin_get("/admin/usage")
    assert s == 200
    print(f"  Admin usage summary: {usage_summary}")
    assert usage_summary["requests"] >= 1
    assert usage_summary["total_tokens"] == total_tokens

    # ---- B: Streaming usage ----
    print("\n--- B: Streaming usage ---")
    # Use raw HTTP to read SSE
    import socket

    s = socket.create_connection(("127.0.0.1", 8001), timeout=30)
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello in one word."},
        ],
        "temperature": 0,
        "max_tokens": 20,
        "stream": True,
    })
    req = (
        f"POST /v1/chat/completions HTTP/1.1\r\n"
        f"Host: {SYN_URL}\r\n"
        f"Content-Type: application/json\r\n"
        f"Authorization: Bearer {api_key}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
        f"{body}"
    ).encode("utf-8")
    s.sendall(req)
    s.settimeout(30)
    resp_data = b""
    while True:
        try:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp_data += chunk
        except socket.timeout:
            break
    s.close()
    text = resp_data.decode("utf-8", errors="replace")
    has_done = "data: [DONE]" in text
    print(f"  Stream completed: {has_done}")
    assert has_done, "Stream should end with [DONE]"

    # Check admin usage increased
    time.sleep(0.5)
    s, usage_summary = admin_get("/admin/usage")
    print(f"  Admin usage after stream: {usage_summary}")
    assert usage_summary["requests"] >= 2

    # ---- C: Rate limit ----
    print("\n--- C: Rate limit (2/min) ---")
    # Set client policy to 2 requests per minute
    s, _ = admin_put(
        f"/admin/clients/{CLIENT_ID}/policy",
        {"requests_per_minute": 2},
    )
    assert s == 200, f"Failed to set policy: {s}"

    # Wait for any previous rate limit window to expire
    # (the rate limiter is in-process, so we need to wait or restart)
    # Since we just set the policy, the rate limiter should have no
    # history for this key yet. But we may have already used some.
    # Let's wait a bit to be safe.
    time.sleep(1.0)

    # Make 2 requests - should succeed
    for i in range(2):
        s, body = http_post(
            "/v1/chat/completions",
            {
                "model": MODEL,
                "messages": [{"role": "user", "content": f"Say {i}"}],
                "temperature": 0,
                "max_tokens": 5,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        print(f"  Request {i+1}: status={s}")
        assert s == 200, f"Expected 200, got {s}"

    # 3rd request should be rate-limited
    s, body = http_post(
        "/v1/chat/completions",
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Say 3"}],
            "temperature": 0,
            "max_tokens": 5,
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    print(f"  Request 3: status={s}, body={body}")
    assert s == 429, f"Expected 429, got {s}"
    assert body["error"]["code"] == "rate_limit_exceeded"

    # Reset the client policy
    admin_put(
        f"/admin/clients/{CLIENT_ID}/policy",
        {"requests_per_minute": 0},
    )

    # ---- D: Request quota ----
    print("\n--- D: Request quota (2/day) ---")
    # Set a very low daily request quota
    s, _ = admin_put(
        f"/admin/clients/{CLIENT_ID}/policy",
        {"requests_per_day": 2},
    )
    assert s == 200

    # We've already made several requests today. The rate limiter is
    # in-process, so we can't easily reset it. But the daily quota is
    # durable, so it should already be at 2+.
    # Let's check the current state.
    s, usage_summary = admin_get("/admin/usage/clients/" + CLIENT_ID)
    print(f"  Current usage: {usage_summary}")
    current_count = usage_summary["requests"]

    # The next request should be rejected (quota exceeded)
    s, body = http_post(
        "/v1/chat/completions",
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "test"}],
            "temperature": 0,
            "max_tokens": 5,
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    print(f"  Request: status={s}, body={body}")
    # We expect either rate_limit_exceeded (if we hit the per-minute limit)
    # or request_quota_exceeded (if we hit the daily limit)
    assert s == 429, f"Expected 429, got {s}"
    assert body["error"]["code"] in ("rate_limit_exceeded", "request_quota_exceeded")

    # Reset the client policy
    admin_put(
        f"/admin/clients/{CLIENT_ID}/policy",
        {"requests_per_day": 0},
    )

    # ---- E: Admin usage endpoint safety ----
    print("\n--- E: Admin usage endpoint safety ---")
    s, body = http_get("/admin/usage")
    assert s == 401, "Admin usage should require auth"
    s, body = http_get(
        "/admin/usage",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert s == 401, "Inference key should not access admin"

    # ---- F: Final state ----
    print("\n--- F: Final state ---")
    s, usage_summary = admin_get("/admin/usage")
    print(f"  Final usage: {usage_summary}")
    assert usage_summary["requests"] > 0
    assert usage_summary["total_tokens"] > 0

    print("\n" + "=" * 60)
    print("ALL M6 CHECKS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
