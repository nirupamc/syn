import httpx, json, time, threading

SYN = "http://127.0.0.1:8001"
ADMIN = {"Authorization": "Bearer test-admin-secret"}
MODEL = r"D:\llama\models\LFM2.5-2.6B-Uncensored-Q4_K_S.gguf"

users = httpx.get(f"{SYN}/admin/users", headers=ADMIN, timeout=10).json()
user = next(u for u in users if u["name"] == "m7-verify-user")
clients = httpx.get(f"{SYN}/admin/clients", headers=ADMIN, timeout=10).json()
client = next(c for c in clients if c["name"] == "m7-verify-client" and c["user_id"] == user["id"])
keys = httpx.get(f"{SYN}/admin/api-keys?client_id={client['id']}", headers=ADMIN, timeout=10).json()
ek = next(k for k in keys if not k.get("revoked_at"))
API_KEY = httpx.post(f"{SYN}/admin/api-keys/{ek['id']}/rotate", headers=ADMIN, timeout=10).json()["key"]
AUTH = {"Authorization": f"Bearer {API_KEY}"}

# confirm max_active=1
status = httpx.get(f"{SYN}/admin/status", headers=ADMIN, timeout=10).json()
print("admission max_active:", status["admission"]["max_active"])

results = {}
def send(idx, payload_extra):
    t0 = time.monotonic()
    try:
        r = httpx.post(f"{SYN}/v1/chat/completions", headers=AUTH,
                       json={"model": MODEL, "messages": [{"role": "user", "content": "Count slowly from 1 to 100, one number per word, no other text."}], "stream": False, **payload_extra},
                       timeout=120)
        results[idx] = (r.status_code, time.monotonic() - t0)
    except Exception as e:
        results[idx] = ("ERR", str(e))

# send A (long generation)
tA = threading.Thread(target=send, args=("A", {}))
tA.start()
time.sleep(0.5)
# observe admission during A
try:
    s1 = httpx.get(f"{SYN}/admin/status", headers=ADMIN, timeout=10).json()["admission"]
    print("during A: active=%s queued=%s" % (s1["active"], s1["queued"]))
except Exception as e:
    print("status during A failed:", e)

# send B (should queue)
tB = threading.Thread(target=send, args=("B", {}))
tB.start()
time.sleep(0.5)
try:
    s2 = httpx.get(f"{SYN}/admin/status", headers=ADMIN, timeout=10).json()["admission"]
    print("during A+B: active=%s queued=%s" % (s2["active"], s2["queued"]))
except Exception as e:
    print("status during A+B failed:", e)

tA.join()
tB.join()
print("A:", results.get("A"))
print("B:", results.get("B"))

# check telemetry for queued request (B)
recent = httpx.get(f"{SYN}/admin/observability/recent?limit=10", headers=ADMIN, timeout=10).json()
b_rec = None
for rec in recent:
    if rec.get("queue_wait_ms") is not None and rec["queue_wait_ms"] > 0:
        b_rec = rec
        break

print("queued telemetry found:", b_rec)

ok = (
    status["admission"]["max_active"] == 1
    and s2["active"] == 1
    and s2["queued"] >= 1
    and b_rec is not None
    and b_rec["queue_wait_ms"] > 0
)
print("Runtime C: PASS" if ok else f"Runtime C: FAIL — max_active={status['admission']['max_active']} active={s2['active']} queued={s2['queued']} b_rec={b_rec}")
