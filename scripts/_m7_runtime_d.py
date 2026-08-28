import httpx, json, socket, time

SYN = "http://127.0.0.1:8001"
SYN_URL = "127.0.0.1:8001"
ADMIN = {"Authorization": "Bearer test-admin-secret"}
MODEL = r"D:\llama\models\LFM2.5-2.6B-Uncensored-Q4_K_S.gguf"

users = httpx.get(f"{SYN}/admin/users", headers=ADMIN, timeout=10).json()
user = next(u for u in users if u["name"] == "m7-verify-user")
clients = httpx.get(f"{SYN}/admin/clients", headers=ADMIN, timeout=10).json()
client = next(c for c in clients if c["name"] == "m7-verify-client" and c["user_id"] == user["id"])
keys = httpx.get(f"{SYN}/admin/api-keys?client_id={client['id']}", headers=ADMIN, timeout=10).json()
ek = next(k for k in keys if not k.get("revoked_at"))
API_KEY = httpx.post(f"{SYN}/admin/api-keys/{ek['id']}/rotate", headers=ADMIN, timeout=10).json()["key"]

# record request_ids already present
before = httpx.get(f"{SYN}/admin/observability/recent?limit=50", headers=ADMIN, timeout=10).json()
before_ids = {r["request_id"] for r in before}

def raw_stream(body):
    s = socket.create_connection(("127.0.0.1", 8001), timeout=60)
    req = (
        "POST /v1/chat/completions HTTP/1.1\r\n"
        f"Host: {SYN_URL}\r\n"
        "Content-Type: application/json\r\n"
        f"Authorization: Bearer {API_KEY}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        f"\r\n{body}"
    ).encode()
    s.sendall(req)
    s.settimeout(0.2)
    return s

body = json.dumps({
    "model": MODEL,
    "messages": [{"role": "user", "content": "Count from 1 to 200, one number per line, with a sentence after each."}],
    "max_tokens": 2000,
    "stream": True,
})
sock = raw_stream(body)

buf = b""
while b"\r\n" not in buf:
    chunk = sock.recv(1024)
    if not chunk:
        break
    buf += chunk
print("status:", buf.split(b"\r\n")[0].decode())

data = b""
chunks = 0
saw_done = False
deadline = time.monotonic() + 25.0
while time.monotonic() < deadline:
    try:
        c = sock.recv(4096)
    except socket.timeout:
        continue
    if not c:
        break
    data += c
    chunks = data.count(b"data: ")
    if b"[DONE]" in data:
        saw_done = True
        break
    if chunks >= 1:
        break

print("chunks before disconnect:", chunks, "saw[DONE]:", saw_done)
sock.close()  # genuine disconnect before [DONE]

# poll up to 15s for cancellation + slot release
new_cancelled = None
active = None
for _ in range(30):
    time.sleep(0.5)
    recent = httpx.get(f"{SYN}/admin/observability/recent?limit=50", headers=ADMIN, timeout=10).json()
    for r in recent:
        if r["request_id"] not in before_ids and r["status"] == "cancelled":
            new_cancelled = r
            break
    if new_cancelled:
        adm = httpx.get(f"{SYN}/admin/status", headers=ADMIN, timeout=10).json()["admission"]
        active = adm["active"]
        if active == 0:
            break

h = httpx.get(f"{SYN}/health", timeout=10)
print("health after disconnect:", h.status_code)
print("new cancelled rec:", new_cancelled)

ok = (
    not saw_done
    and chunks >= 1
    and h.status_code == 200
    and new_cancelled is not None
)
print("Runtime D: PASS" if ok else f"Runtime D: FAIL — saw_done={saw_done} chunks={chunks} health={h.status_code} new_cancelled={new_cancelled}")
