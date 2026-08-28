import httpx

SYN = "http://127.0.0.1:8001"
ADMIN = {"Authorization": "Bearer test-admin-secret"}
MODEL = r"D:\llama\models\LFM2.5-2.6B-Uncensored-Q4_K_S.gguf"

# bootstrap creds
ur = httpx.post(f"{SYN}/admin/users", headers=ADMIN, json={"name": "m7-verify-user"}, timeout=10)
if ur.status_code == 409:
    users = httpx.get(f"{SYN}/admin/users", headers=ADMIN, timeout=10).json()
    user = next(u for u in users if u["name"] == "m7-verify-user")
else:
    user = ur.json()
clients = httpx.get(f"{SYN}/admin/clients", headers=ADMIN, timeout=10).json()
client = next((c for c in clients if c["name"] == "m7-verify-client" and c["user_id"] == user["id"]), None)
if client is None:
    client = httpx.post(f"{SYN}/admin/clients", headers=ADMIN, json={"user_id": user["id"], "name": "m7-verify-client"}, timeout=10).json()
keys = httpx.get(f"{SYN}/admin/api-keys?client_id={client['id']}", headers=ADMIN, timeout=10).json()
ek = next((k for k in keys if not k.get("revoked_at")), None)
if ek:
    API_KEY = httpx.post(f"{SYN}/admin/api-keys/{ek['id']}/rotate", headers=ADMIN, timeout=10).json()["key"]
else:
    API_KEY = httpx.post(f"{SYN}/admin/api-keys", headers=ADMIN, json={"client_id": client["id"], "name": "m7-verify-key"}, timeout=10).json()["key"]
AUTH = {"Authorization": f"Bearer {API_KEY}"}

resp = httpx.post(
    f"{SYN}/v1/chat/completions",
    headers=AUTH,
    json={"model": MODEL, "messages": [{"role": "user", "content": "Say hello in 3 words."}], "stream": False},
    timeout=60,
)
print("chat status:", resp.status_code)
body = resp.json()
print("tokens:", body.get("usage"))

summary = httpx.get(f"{SYN}/admin/observability/summary", headers=ADMIN, timeout=10).json()
print("summary completed:", summary["requests"]["completed"])
print("summary tokens:", summary["tokens"]["total_tokens"])
print("summary latency:", summary["latency"])

recent = httpx.get(f"{SYN}/admin/observability/recent?limit=1", headers=ADMIN, timeout=10).json()
rec = recent[0]
print("recent status:", rec["status"], "total_duration_ms:", rec["total_duration_ms"], "total_tokens:", rec["total_tokens"])

ok = (
    resp.status_code == 200
    and summary["requests"]["completed"] >= 1
    and rec["status"] == "completed"
    and rec["total_tokens"] is not None
    and rec["total_duration_ms"] is not None
)
print("Runtime A: PASS" if ok else f"Runtime A: FAIL — completed={summary['requests']['completed']} rec={rec}")
