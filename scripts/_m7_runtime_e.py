import httpx, sys

SYN = "http://127.0.0.1:8001"
ADMIN = {"Authorization": "Bearer test-admin-secret"}
MODEL = r"D:\llama\models\LFM2.5-2.6B-Uncensored-Q4_K_S.gguf"

phase = sys.argv[1] if len(sys.argv) > 1 else "healthy"
expect_reachable = (phase == "healthy")

users = httpx.get(f"{SYN}/admin/users", headers=ADMIN, timeout=10).json()
user = next(u for u in users if u["name"] == "m7-verify-user")
clients = httpx.get(f"{SYN}/admin/clients", headers=ADMIN, timeout=10).json()
client = next(c for c in clients if c["name"] == "m7-verify-client" and c["user_id"] == user["id"])
keys = httpx.get(f"{SYN}/admin/api-keys?client_id={client['id']}", headers=ADMIN, timeout=10).json()
ek = next(k for k in keys if not k.get("revoked_at"))
API_KEY = httpx.post(f"{SYN}/admin/api-keys/{ek['id']}/rotate", headers=ADMIN, timeout=10).json()["key"]
AUTH = {"Authorization": f"Bearer {API_KEY}"}

# backend health via /health
h = httpx.get(f"{SYN}/health", timeout=10).json()
print("health backend:", h["backend"])
reachable = h["backend"]["reachable"]
state = h["backend"]["state"]

# try inference
try:
    r = httpx.post(f"{SYN}/v1/chat/completions", headers=AUTH,
                   json={"model": MODEL, "messages": [{"role": "user", "content": "Say hi."}], "stream": False},
                   timeout=30)
    inf_status = r.status_code
    inf_ok = r.status_code == 200
    inf_body = r.text[:120]
except Exception as e:
    inf_status = "ERR"
    inf_ok = False
    inf_body = str(e)[:120]

print("inference:", inf_status, inf_body)

ok = (reachable == expect_reachable) and (state == ("reachable" if expect_reachable else "unreachable"))
if expect_reachable:
    ok = ok and inf_ok
else:
    ok = ok and (not inf_ok)  # inference must fail cleanly

print(f"PHASE {phase}: {'OK' if ok else 'FAIL'} (reachable={reachable} state={state} inf={inf_status})")
sys.exit(0 if ok else 1)
