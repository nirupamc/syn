# Syn — Secure Remote Deployment (M8)

This guide describes the **intended and verified** M8 deployment architecture:
Syn is remotely reachable via **Cloudflare Tunnel (HTTPS)**, while `llama.cpp`
remains **loopback-only** on the host. All existing Syn guarantees
(auth, quotas, streaming, observability) are preserved.

```text
REMOTE CLIENT (phone/laptop/VM)
        │
        │ HTTPS (TLS terminated at Cloudflare)
        ▼
   Cloudflare Edge
        │
        │ outbound-only tunnel (no inbound firewall rule)
        ▼
   cloudflared (on Syn host, connects to 127.0.0.1:8001)
        │
        │ HTTP loopback
        ▼
   Syn Gateway — 127.0.0.1:8001
        │
        │ HTTP loopback ONLY
        ▼
   llama.cpp — 127.0.0.1:8080
        │
        ▼
   GPU (NVIDIA RTX 5060 Ti 16GB)
```

**Critical invariant:** `llama.cpp` is NEVER exposed publicly. No router port-forward to `8080`.

---

## 1. Prerequisites

* Windows host with Syn repo (`E:\cyn`), Python 3.11+, `.venv`, SQLite.
* `llama-server` binary (`D:\llama\llama-server.exe`) and model (`D:\llama\models\LFM2.5-2.6B-Uncensored-Q4_K_S.gguf`).
* `cloudflared` installed (`cloudflared --version` → `2026.8.2`) — do NOT vendor into repo.
* A Cloudflare account with a zone/DNS you control.
* A Syn bootstrap admin secret (`SYN_ADMIN_SECRET`) in `.env`.

---

## 2. Start llama.cpp (loopback-only)

```powershell
# Always bind loopback explicitly
D:\llama\llama-server.exe `
  --model D:\llama\models\LFM2.5-2.6B-Uncensored-Q4_K_S.gguf `
  --host 127.0.0.1 --port 8080 `
  --ctx-size 8192
# Verify
Invoke-RestMethod http://127.0.0.1:8080/health          # → {"status":"ok"}
Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 8080
# Expected: LocalAddress 127.0.0.1, NOT 0.0.0.0
```

If `0.0.0.0:8080` appears, STOP and fix `--host`.

---

## 3. Start Syn (loopback-only)

```powershell
# .env must contain SYN_ADMIN_SECRET and defaults already ship loopback binding
# SYN_HOST=127.0.0.1, SYN_PORT=8001, SYN_BACKEND_BASE_URL=http://127.0.0.1:8080
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8001
# Verify local
Invoke-RestMethod http://127.0.0.1:8001/health
# → {"status":"ok","backend":{"configured":true,"reachable":true,"state":"reachable"}}
Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 8001
# Expected: 127.0.0.1:8001
```

Syn still enforces `Bearer API key` auth, model permissions, quotas, admission.

---

## 4. Verify local health before tunneling

```powershell
# No tunnel needed for these
Invoke-RestMethod http://127.0.0.1:8001/health
Invoke-RestMethod http://127.0.0.1:8001/health/ready
# backend.reason health check must be "healthy" when llama is up
```

---

## 5. Create / configure Cloudflare Tunnel (named tunnel — preferred)

```powershell
# Authenticate once (opens browser, stores cert outside repo)
cloudflared tunnel login
# Create named tunnel
cloudflared tunnel create syn-m8
# Route DNS (example)
cloudflared tunnel route dns syn-m8 syn.example.com
# Config lives OUTSIDE repo, e.g. C:\Users\<you>\.cloudflared\config.yml
# Use deploy/cloudflared.example.yml as a template (placeholders only)
Copy-Item deploy\cloudflared.example.yml $env:USERPROFILE\.cloudflared\config.yml
# Edit config.yml: set tunnel ID, credentials-file (outside repo), hostname
# ingress: hostname: syn.example.com → http://127.0.0.1:8001
```

**Do NOT commit** the real tunnel ID, JSON credentials, or token. `deploy/cloudflared.example.yml` contains placeholders only.

Quick Tunnel (`cloudflared tunnel --url http://127.0.0.1:8001`) is acceptable for a smoke test but **not** the final M8 deployment because long-lived SSE streaming should be validated against the intended named-tunnel setup.

---

## 6. Run tunnel

```powershell
cloudflared tunnel run syn-m8
# Logs: "Registered tunnel connection", "Route propagating"
# Ingress: syn.example.com → http://127.0.0.1:8001
```

`cloudflared` creates **outbound-only** connections to Cloudflare; no inbound firewall rule for `8001`/`8080` is required.

---

## 7. Obtain remote HTTPS URL

After `route dns`, the public URL is:

```text
https://syn.example.com
```

TLS is terminated at Cloudflare; traffic between Cloudflare and `cloudflared` is encrypted; inside the host boundary Syn remains `http://127.0.0.1:8001`.

---

## 8. Issue Syn API key (once, via admin plane)

```powershell
$admin = @{ "X-Admin-Secret" = "test-admin-secret" }  # use real SYN_ADMIN_SECRET
# Already have m7-verify-user/client? Reuse or create new via /admin/*:
# POST /admin/users, POST /admin/clients, POST /admin/api-keys
# → syn_live_<8>-<43> (store securely, shown once)
```

---

## 9. Configure remote client (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://syn.example.com/v1",
    api_key="syn_live_...",  # issued in step 8
)

# Non-streaming
resp = client.chat.completions.create(
    model="D:\\llama\\models\\LFM2.5-2.6B-Uncensored-Q4_K_S.gguf",
    messages=[{"role": "user", "content": "Say hi in one sentence."}],
    max_tokens=64,
)
print(resp.choices[0].message.content)
print(resp.usage)

# Streaming (SSE through Cloudflare)
stream = client.chat.completions.create(
    model="D:\\llama\\models\\LFM2.5-2.6B-Uncensored-Q4_K_S.gguf",
    messages=[{"role": "user", "content": "Stream a short story."}],
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

No Syn-specific client is required.

---

## 10. Verify remote health (safe)

```powershell
Invoke-RestMethod https://syn.example.com/health
# → 200, {"status":"ok","backend":{"reachable":true,"state":"reachable"}}
# Must NOT expose filesystem paths, secrets, stack traces. Only operational state.
```

---

## 11. Negative tests (remote)

```python
import httpx
base="https://syn.example.com"
# No key → 401
httpx.get(f"{base}/v1/models").status_code  # 401
# Invalid key → 401
httpx.get(f"{base}/v1/models", headers={"Authorization":"Bearer syn_live_invalid_…"}).status_code
# Valid key → 200
httpx.get(f"{base}/v1/models", headers={"Authorization": f"Bearer {valid}"}).status_code
# Oversized body → 413 (SYN_MAX_REQUEST_BODY_BYTES, default 1 MiB)
httpx.post(f"{base}/v1/chat/completions", headers={"Authorization":f"Bearer {valid}"}, json={"model":mid,"messages":[{"role":"user","content":"x"*2_000_000}]})
# Forbidden model (M3 policy) → 403
```

---

## 12. Observability remote

After a remote inference, locally (or via admin auth through tunnel):

```powershell
$h=@{"X-Admin-Secret"="…"}
Invoke-RestMethod https://syn.example.com/admin/observability/recent?limit=1 -Headers $h
Invoke-RestMethod https://syn.example.com/admin/observability/summary -Headers $h
Invoke-RestMethod https://syn.example.com/admin/metrics -Headers $h
```

Recent entries show `request_id`, `model`, `status`, `total_duration_ms`, `ttft_ms`, tokens — **no prompts/responses**, no keys.

Dashboard:

```powershell
Invoke-WebRequest https://syn.example.com/admin/dashboard -Headers $h
# → 200 text/html, contains backend/active/queued/completed/failed/cancelled/tokens/latency/TTFT/recent, no secrets
```

---

## 13. Raw llama.cpp must remain unreachable

From the **remote** device (phone/laptop on another network), attempt:

```powershell
# Should FAIL (timeout / connection refused)
Invoke-WebRequest http://<host-public-ip>:8080/health -TimeoutSec 5
Test-NetConnection <host-public-ip> -Port 8080
```

Expected: unreachable. **Do not** port-forward `8080` to prove this.

---

## 14. Tunnel failure behavior

```powershell
# Stop tunnel: Ctrl+C cloudflared
Invoke-RestMethod https://syn.example.com/health  # → fails (tunnel down)
Invoke-RestMethod http://127.0.0.1:8001/health     # → still 200 (local Syn alive)
# Restart tunnel → remote works again, no Syn restart needed
```

---

## 15. Security controls summary (M8)

* **Auth:** Bearer API key mandatory on `/v1/*`; `X-Admin-Secret` on `/admin/*`. Cloudflare does NOT replace auth.
* **Request size:** `SYN_MAX_REQUEST_BODY_BYTES` (default 1048576). `Content-Length` > limit → `413 request_body_too_large` (OpenAI envelope on `/v1/*`).
* **CORS:** `SYN_CORS_ALLOWED_ORIGINS` (comma-separated). Default empty → **no CORS headers** (restrictive). Wildcard `*` rejected in config. Allowed origins receive `Access-Control-Allow-Origin: <origin>` only for exact matches.
* **TLS:** `https://` at edge (Cloudflare); inside host `http://127.0.0.1:8001` is acceptable. Document accurately.
* **Proxy headers:** M8 **does not trust** `X-Forwarded-For`, `X-Forwarded-Proto`, `CF-Connecting-IP` for security. Rate limiting remains **identity-based** (`api_key_id`), not IP-based.
* **Admin:** stays protected by `SYN_ADMIN_SECRET`; optionally operators may add Cloudflare Access later (not required for M8).
* **Errors:** never expose tracebacks, paths, tokens, admin secret, `Authorization`, or Cloudflare credentials. `SynError` envelope only.
* **Health:** public `/health` exposes only `configured`/`reachable`/`state`/`reason`; no secrets.
* **Network boundary:** `127.0.0.1:8001` and `127.0.0.1:8080` verified via `Get-NetTCPConnection -State Listen`.

---

## 16. Browser vs server-to-server

* **Server-to-server (Python/httpx/OpenAI SDK):** no CORS needed; default restrictive CORS is fine.
* **Browser fetch:** set `SYN_CORS_ALLOWED_ORIGINS="https://your-frontend.example.com"` explicitly. Do not use `*` with credentials. Preflight `OPTIONS` is handled by `CORSMiddleware`.

---

## 17. Timeouts

* Syn: `SYN_BACKEND_TIMEOUT_SECONDS=120`, `SYN_BACKEND_CONNECT_TIMEOUT_SECONDS=10`, `SYN_BACKEND_HEALTH_TIMEOUT_SECONDS=5`. Streaming uses `connect_timeout` only (no overall timeout mid-stream; chunk arrival is liveness).
* Cloudflare: keep tunnel `connectTimeout: 30s`; do not set a short `originRequest.timeout` that kills valid streams. Observed: named tunnel preserves incremental SSE; Quick Tunnel may buffer — prefer named.

---

## 18. Listener audit (Windows)

```powershell
Get-NetTCPConnection -State Listen | Format-Table LocalAddress,LocalPort,State,OwningProcess -AutoSize
# After deployment, only:
# 127.0.0.1:8001 (Syn, PID …)
# 127.0.0.1:8080 (llama.cpp, PID …)
# Cloudflared has no LISTEN on 0.0.0.0 for these ports; it dials out.
```

Document exact output in handoff.

---

## 19. Known limitations (M8)

* Single-process only (`--workers 1`) for admission/rate/observability correctness.
* Admin plane is still shared-secret (not full RBAC); tunnel does not add user DB.
* `llama.cpp` scheduler remains independent; Syn limits **concurrent HTTP requests**, not GPU batching.
* No load balancing, no Redis, no Kubernetes, no billing, no OAuth/SSO.

---

## 20. Do NOT commit

* `.env`, `cloudflared` credentials JSON, tunnel token, real hostname, `*.pem`/`*.key`, `data/*.db`, `*.log`, `*.gguf`.

`deploy/cloudflared.example.yml` and this doc contain **placeholders only**.

---

## 21. Handoff evidence

M8 handoff must include the `Get-NetTCPConnection` listener dump, remote `https://` health, remote SDK `200` and streaming chunk counts, `401` without key, `413` oversized, observability entry, and raw `host:8080` unreachable proof. See `README.md` Roadmap and `docs/ARCHITECTURE.md` §19 for M8 architecture summary.
