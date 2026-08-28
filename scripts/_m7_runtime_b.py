import httpx, json, time

SYN = "http://127.0.0.1:8001"
ADMIN = {"Authorization": "Bearer test-admin-secret"}
MODEL = r"D:\llama\models\LFM2.5-2.6B-Uncensored-Q4_K_S.gguf"

# reuse bootstrap (creds already exist)
users = httpx.get(f"{SYN}/admin/users", headers=ADMIN, timeout=10).json()
user = next(u for u in users if u["name"] == "m7-verify-user")
clients = httpx.get(f"{SYN}/admin/clients", headers=ADMIN, timeout=10).json()
client = next(c for c in clients if c["name"] == "m7-verify-client" and c["user_id"] == user["id"])
keys = httpx.get(f"{SYN}/admin/api-keys?client_id={client['id']}", headers=ADMIN, timeout=10).json()
ek = next(k for k in keys if not k.get("revoked_at"))
API_KEY = httpx.post(f"{SYN}/admin/api-keys/{ek['id']}/rotate", headers=ADMIN, timeout=10).json()["key"]
AUTH = {"Authorization": f"Bearer {API_KEY}"}

# real streaming: iterate SSE chunks, record first content chunk time
t0 = time.monotonic()
first_content = None
chunks = 0
with httpx.stream(
    "POST",
    f"{SYN}/v1/chat/completions",
    headers=AUTH,
    json={"model": MODEL, "messages": [{"role": "user", "content": "Write a short poem about the sea."}], "stream": True},
    timeout=60,
) as r:
    print("stream status:", r.status_code)
    for line in r.iter_lines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        chunks += 1
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        choices = obj.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            c = delta.get("content")
            if c and first_content is None:
                first_content = time.monotonic() - t0

print("first content chunk at +%.0fms" % (first_content * 1000) if first_content else "NO CONTENT CHUNK")
print("total chunks:", chunks)

# verify telemetry
recent = httpx.get(f"{SYN}/admin/observability/recent?limit=5", headers=ADMIN, timeout=10).json()
stream_rec = next((x for x in recent if x["streaming"]), None)
print("stream rec:", stream_rec["status"], "ttft_ms:", stream_rec["ttft_ms"], "total_duration_ms:", stream_rec["total_duration_ms"], "total_tokens:", stream_rec["total_tokens"])

ok = (
    r.status_code == 200
    and first_content is not None
    and chunks > 1
    and stream_rec is not None
    and stream_rec["ttft_ms"] is not None
    and stream_rec["ttft_ms"] > 0
)
print("Runtime B: PASS" if ok else f"Runtime B: FAIL — first_content={first_content} chunks={chunks} rec={stream_rec}")
