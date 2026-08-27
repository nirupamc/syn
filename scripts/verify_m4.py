"""M4 Runtime Verification Script.

Tests Syn's admission control with a real llama.cpp backend.
"""
import json
import sys
import time
import urllib.request
import urllib.error
import threading


SYN_URL = "http://127.0.0.1:8001"
ADMIN_SECRET = "test-admin-secret"
API_KEY = "syn_live_aIWEWyey_YzPeuNuLYHnADYo2onhXlQzsKYdhVhlNncp9iBLv0zM"


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


def get_status():
    s, b = http_get("/admin/status", headers={"X-Admin-Secret": ADMIN_SECRET})
    return b.get("admission", {})


def chat_request(req_id, results, event_start=None):
    """Send a chat request and store the result."""
    if event_start:
        event_start.wait()
    s, b = http_post(
        "/v1/chat/completions",
        {
            "model": "models\\gpt-oss-20b-Q4_K_M.gguf",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": f"Reply with the number {req_id} and nothing else."},
            ],
            "temperature": 0,
            "max_tokens": 50,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    results[req_id] = (s, b)


def main():
    print("=" * 60)
    print("M4 Runtime Verification")
    print("=" * 60)

    # ---- A: Normal single request ----
    print("\n--- A: Normal single request ---")
    status = get_status()
    print(f"  Initial status: {status}")

    s, b = http_post(
        "/v1/chat/completions",
        {
            "model": "models\\gpt-oss-20b-Q4_K_M.gguf",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Reply with exactly: SYN_OK"},
            ],
            "temperature": 0,
            "max_tokens": 50,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert s == 200, f"Expected 200, got {s}: {b}"
    content = b["choices"][0]["message"]["content"]
    print(f"  Response: {content!r}")

    # ---- B: Concurrent requests (active limit=1) ----
    print("\n--- B: Concurrent requests (max_active=1) ---")
    # Launch 3 concurrent requests. With max_active=1 and max_queue=2,
    # we expect: 1 running, 2 queued.
    results: dict[str, tuple[int, dict]] = {}
    start_event = threading.Event()
    threads = []
    for i in range(3):
        t = threading.Thread(
            target=chat_request, args=(f"r{i}", results, start_event)
        )
        t.start()
        threads.append(t)

    # Start all at once
    start_event.set()

    # Poll the status endpoint until we see the expected state or timeout
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        status = get_status()
        if status["active"] == 1 and status["queued"] == 2:
            break
        time.sleep(0.1)
    else:
        status = get_status()
        print(f"  Status (timeout): {status}")
        assert False, f"Expected active=1, queued=2, got {status}"

    # Check status: should be 1 active, 2 queued
    status = get_status()
    print(f"  Status during run: {status}")
    assert status["active"] == 1, f"Expected active=1, got {status}"
    assert status["queued"] == 2, f"Expected queued=2, got {status}"

    # Wait for all to complete
    for t in threads:
        t.join(timeout=60)

    # All should succeed
    for i in range(3):
        s, b = results[f"r{i}"]
        assert s == 200, f"r{i} failed: {s} {b}"

    # Status should be back to 0/0
    status = get_status()
    print(f"  Status after drain: {status}")
    assert status["active"] == 0
    assert status["queued"] == 0

    # ---- C: Queue full ----
    print("\n--- C: Queue full (max_active=1, max_queue=2) ---")
    # Launch 4 concurrent requests. With max_active=1 and max_queue=2,
    # we expect: 1 running, 2 queued, 1 rejected with 429.
    results2: dict[str, tuple[int, dict]] = {}
    start_event2 = threading.Event()
    threads2 = []
    for i in range(4):
        t = threading.Thread(
            target=chat_request, args=(f"r{i}", results2, start_event2)
        )
        t.start()
        threads2.append(t)

    start_event2.set()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        status = get_status()
        if status["active"] == 1 and status["queued"] == 2:
            break
        time.sleep(0.1)
    else:
        status = get_status()
        print(f"  Status (timeout): {status}")
        assert False, f"Expected active=1, queued=2, got {status}"

    status = get_status()
    print(f"  Status during run: {status}")
    assert status["active"] == 1
    assert status["queued"] == 2

    # Wait for all
    for t in threads2:
        t.join(timeout=60)

    # Check results
    statuses = [results2[f"r{i}"][0] for i in range(4)]
    print(f"  Result statuses: {statuses}")
    # Exactly one should be 429
    queue_full_count = sum(1 for s in statuses if s == 429)
    assert queue_full_count == 1, f"Expected 1 queue_full, got {queue_full_count}"

    # ---- D: Backend error recovery ----
    print("\n--- D: Backend error does not leak capacity ---")
    # This is hard to test without killing the backend. Skip for now.
    # The isolated tests already cover this.
    print("  (covered by isolated tests)")

    # ---- E: Status endpoint ----
    print("\n--- E: Status endpoint ---")
    status = get_status()
    print(f"  Final status: {status}")
    assert status["active"] == 0
    assert status["queued"] == 0
    assert status["max_active"] == 1
    assert status["max_queue"] == 2

    print("\n" + "=" * 60)
    print("ALL CHECKS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
