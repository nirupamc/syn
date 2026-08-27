"""M5 Runtime Verification Script.

Tests Syn's streaming with a real llama.cpp backend.
"""
import json
import sys
import time
import urllib.request
import urllib.error
import threading


SYN_URL = "http://127.0.0.1:8001"
ADMIN_SECRET = "test-admin-secret"
API_KEY = "syn_live_lx0qdGLd_XHgq-q5GD-TSlUiMWiMG1iWsr0DUYMQe3bkmB_meyBc"


def http_get(path, headers=None):
    url = f"{SYN_URL}{path}"
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def get_status():
    s, b = http_get("/admin/status", headers={"X-Admin-Secret": ADMIN_SECRET})
    return b.get("admission", {})


def main():
    print("=" * 60)
    print("M5 Runtime Verification")
    print("=" * 60)

    # ---- A: Standard stream via OpenAI SDK ----
    print("\n--- A: Standard stream via OpenAI SDK ---")
    from openai import OpenAI

    client = OpenAI(base_url=f"{SYN_URL}/v1", api_key=API_KEY)

    start = time.monotonic()
    first_chunk_time = None
    chunks = []
    full_content = ""
    try:
        stream = client.chat.completions.create(
            model="models\\gpt-oss-20b-Q4_K_M.gguf",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Count from 1 to 5, one number per line."},
            ],
            temperature=0,
            max_tokens=100,
            stream=True,
        )
        for chunk in stream:
            now = time.monotonic()
            if first_chunk_time is None:
                first_chunk_time = now - start
            content = ""
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
            if content:
                full_content += content
            chunks.append(chunk)
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    elapsed = time.monotonic() - start
    print(f"  Chunks received: {len(chunks)}")
    print(f"  First chunk at: {first_chunk_time:.2f}s")
    print(f"  Total time: {elapsed:.2f}s")
    print(f"  Content: {full_content!r}")

    # ---- B: Raw curl-style SSE ----
    print("\n--- B: Raw SSE via HTTP ---")
    req = urllib.request.Request(
        f"{SYN_URL}/v1/chat/completions",
        data=json.dumps({
            "model": "models\\gpt-oss-20b-Q4_K_M.gguf",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say hello"},
            ],
            "temperature": 0,
            "max_tokens": 20,
            "stream": True,
        }).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"  Content-Type: {resp.headers.get('content-type')}")
            body = resp.read().decode("utf-8")
            lines = body.split("\n")
            data_lines = [l for l in lines if l.startswith("data: ")]
            print(f"  Data lines: {len(data_lines)}")
            # Show first 3 and last 3
            for l in data_lines[:3]:
                print(f"    {l[:80]}")
            if len(data_lines) > 3:
                print("    ...")
                for l in data_lines[-3:]:
                    print(f"    {l[:80]}")
    except urllib.error.HTTPError as e:
        print(f"  Error: {e.code} {e.read().decode()}")
        sys.exit(1)

    # ---- C: Admission slot during stream ----
    print("\n--- C: Admission slot during stream ---")
    # We can't easily test this with the SDK synchronously.
    # We'll use a threaded approach: start a stream, check status, then read.
    status_result = {}

    def stream_and_check():
        req = urllib.request.Request(
            f"{SYN_URL}/v1/chat/completions",
            data=json.dumps({
                "model": "models\\gpt-oss-20b-Q4_K_M.gguf",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Write a short poem about cats."},
                ],
                "temperature": 0,
                "max_tokens": 100,
                "stream": True,
            }).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}",
            },
        )
        with urllib.request.urlopen(req) as resp:
            # Read first byte to ensure stream has started
            chunk = resp.read(1)
            # Now check status - should show active=1
            time.sleep(0.1)
            status_result["during"] = get_status()
            # Read rest
            resp.read()
        status_result["after"] = get_status()

    t = threading.Thread(target=stream_and_check)
    t.start()
    t.join(timeout=30)

    if "during" in status_result:
        print(f"  During stream: {status_result['during']}")
        # Note: with max_active=1 and the stream being the only request,
        # active should be 1.
    if "after" in status_result:
        print(f"  After stream: {status_result['after']}")
        assert status_result["after"]["active"] == 0, "Slot not released after stream"

    # ---- D: Final status check ----
    print("\n--- D: Final status ---")
    final = get_status()
    print(f"  {final}")
    assert final["active"] == 0
    assert final["queued"] == 0

    print("\n" + "=" * 60)
    print("ALL CHECKS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
