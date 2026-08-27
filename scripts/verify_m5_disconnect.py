"""M5 Disconnect Runtime Verification (v2).

Uses a longer prompt to ensure the stream is actually running when
we disconnect, so we can observe the active=1 state and the queue
drain after disconnect.
"""
import json
import socket
import sys
import threading
import time
import urllib.request
import urllib.error


SYN_URL = "127.0.0.1:8001"
ADMIN_SECRET = "test-admin-secret"


def http_post(path, data, headers=None):
    url = f"http://{SYN_URL}{path}"
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
    if len(token) > 17:
        return token[:17] + "...REDACTED"
    return token


def get_status():
    req = urllib.request.Request(
        f"http://{SYN_URL}/admin/status",
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["admission"]


def raw_http_stream(path, body, headers, timeout=30.0):
    """Open a raw HTTP connection and return the socket."""
    s = socket.create_connection(("127.0.0.1", 8001), timeout=timeout)
    req = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: {SYN_URL}\r\n"
        f"Content-Type: application/json\r\n"
        f"Authorization: {headers['Authorization']}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
        f"{body}"
    ).encode("utf-8")
    s.sendall(req)
    s.settimeout(timeout)
    return s


def read_for(sock, duration):
    """Read from socket for a given duration, return all data."""
    sock.settimeout(0.1)
    data = b""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        except socket.timeout:
            continue
        except Exception:
            break
    return data


def main():
    print("=" * 60)
    print("M5 Disconnect Runtime Verification (v2)")
    print("=" * 60)

    # ---- Bootstrap credentials ----
    print("\n--- Bootstrap credentials ---")
    s, user = admin_post("/admin/users", {"name": "m5dc-test-user"})
    assert s == 201, f"create user failed: {s}"
    user_id = user["id"]
    s, client_obj = admin_post("/admin/clients", {"user_id": user_id, "name": "m5dc-test-client"})
    assert s == 201, f"create client failed: {s}"
    client_id = client_obj["id"]
    s, key_out = admin_post("/admin/api-keys", {"client_id": client_id, "name": "m5dc-test-key"})
    assert s == 201, f"create key failed: {s}"
    api_key = key_out["key"]
    print(f"  API key created: {redact(api_key)}")

    # ---- Check initial state ----
    print("\n--- Initial state ---")
    print(f"  {get_status()}")

    # ---- Start stream A (very long-running) ----
    print("\n--- Starting stream A (long generation) ---")
    # Use a very high max_tokens to force a long generation
    body_a = json.dumps({
        "model": "models\\gpt-oss-20b-Q4_K_M.gguf",
        "messages": [
            {"role": "system", "content": "You are a verbose storyteller."},
            {"role": "user", "content": "Count slowly from 1 to 100, one number per line, with a sentence between each."},
        ],
        "temperature": 0,
        "max_tokens": 2000,
        "stream": True,
    })

    sock_a = raw_http_stream(
        "/v1/chat/completions",
        body_a,
        {"Authorization": f"Bearer {api_key}"},
    )

    # Read response status
    status_line = b""
    while b"\r\n" not in status_line:
        chunk = sock_a.recv(1024)
        if not chunk:
            break
        status_line += chunk
    print(f"  Status line: {status_line.split(b'\\r\\n')[0].decode()}")

    # Read some data to ensure stream is active
    data_a = read_for(sock_a, 2.0)
    chunk_count_a = data_a.count(b"data: ")
    print(f"  Chunks received from A: {chunk_count_a}")

    # Check admission state - A should be active
    time.sleep(0.1)
    status = get_status()
    print(f"  Admission state (A should be active): {status}")

    # ---- Start request B (non-streaming, will queue) ----
    print("\n--- Starting request B (should queue) ---")
    body_b = json.dumps({
        "model": "models\\gpt-oss-20b-Q4_K_M.gguf",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say the word 'hello' and nothing else."},
        ],
        "temperature": 0,
        "max_tokens": 20,
    }).encode("utf-8")

    b_result = {}
    b_start_time = None

    def do_b():
        nonlocal b_start_time
        try:
            b_start_time = time.monotonic()
            req = urllib.request.Request(
                f"http://{SYN_URL}/v1/chat/completions",
                data=body_b,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                b_result["status"] = resp.status
                b_result["body"] = json.loads(resp.read())
                b_result["completed_at"] = time.monotonic()
        except Exception as e:
            b_result["error"] = str(e)

    t_b = threading.Thread(target=do_b)
    t_b.start()

    # Give B time to enter the queue
    time.sleep(0.5)
    status = get_status()
    print(f"  Admission state (B should be queued): {status}")

    # ---- Disconnect client A ----
    print("\n--- Disconnecting client A ---")
    disconnect_time = time.monotonic()
    sock_a.close()
    print(f"  Client A socket closed at t={disconnect_time:.2f}")

    # ---- Verify Syn is still alive ----
    print("\n--- Verifying Syn is still alive ---")
    health = json.loads(urllib.request.urlopen(f"http://{SYN_URL}/health").read())
    print(f"  Health: {health['status']}")
    assert health["status"] == "ok", "Syn is not alive after disconnect"

    # ---- Wait for A's slot to release ----
    print("\n--- Waiting for A's slot to release ---")
    deadline = time.monotonic() + 15.0
    slot_released_time = None
    while time.monotonic() < deadline:
        status = get_status()
        if status["active"] == 0:
            slot_released_time = time.monotonic()
            break
        time.sleep(0.1)

    if slot_released_time:
        print(f"  Slot released at t={slot_released_time:.2f} "
              f"(+{slot_released_time - disconnect_time:.2f}s after disconnect)")
    else:
        print("  WARNING: Slot not released within timeout")

    status = get_status()
    print(f"  Final admission state: {status}")

    # ---- Wait for B to complete ----
    print("\n--- Waiting for B to complete ---")
    t_b.join(timeout=30)
    if "error" in b_result:
        print(f"  B error: {b_result['error']}")
        sys.exit(1)

    b_completed_time = b_result.get("completed_at", time.monotonic())
    b_wait_time = b_completed_time - b_start_time if b_start_time else 0
    print(f"  B status: {b_result['status']}")
    print(f"  B wait time: {b_wait_time:.2f}s")
    if b_result['status'] == 200:
        content = b_result['body']['choices'][0]['message']['content']
        print(f"  B content: {content!r}")

    # ---- Final state ----
    print("\n--- Final state ---")
    status = get_status()
    print(f"  {status}")
    assert status["active"] == 0
    assert status["queued"] == 0

    # ---- Verify Syn health one more time ----
    health = json.loads(urllib.request.urlopen(f"http://{SYN_URL}/health").read())
    assert health["status"] == "ok"
    print(f"  Syn health: {health['status']} (healthy)")

    print("\n" + "=" * 60)
    print("ALL DISCONNECT CHECKS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
