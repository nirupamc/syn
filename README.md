# Syn

**Syn** is a self-hosted LLM inference gateway / control plane. It sits between
your applications and private LLM inference backends, exposing a single
OpenAI-compatible API while handling authentication, admission control,
concurrency, usage accounting, observability, and model/backend routing.

```text
Applications / Agents
        ↓
OpenAI-compatible API
        ↓
Syn
        ↓
Authentication / Policy · Admission Control · Queueing / Concurrency
Usage Accounting · Observability · Model / Backend Routing
        ↓
Private inference backend
        ↓
llama.cpp
        ↓
NVIDIA RTX 5060 Ti 16GB
```

The user-facing contract is deliberately OpenAI-compatible:

```text
OPENAI_BASE_URL=https://llm.example.com/v1
OPENAI_API_KEY=<issued-key>
MODEL=<model-alias>
```

Applications never need to know where or how a model is hosted.

---

## What Syn IS

- an inference **gateway** and **control plane**
- an **OpenAI-compatible API facade**
- an **authentication / authorization boundary**
- an **admission-control** layer
- a **request scheduling** layer
- a **usage-accounting** layer
- an **observability** layer
- a **backend / model abstraction** layer

## What Syn IS NOT

- an inference engine (llama.cpp does the inference)
- a replacement for llama.cpp
- an agent framework
- a RAG framework
- a model-training system
- a GPU-virtualization system
- a complete clone of the OpenAI API
- a Kubernetes / distributed system (at this stage)

**Actual LLM inference belongs to inference backends such as llama.cpp.** Syn
routes to them and protects them; it does not replace them.

---

## Status

Current milestone: **M10 — Admin Control Plane UI** *(in progress)*.

> Milestone status rules: the only allowed statuses are `NOT STARTED`,
> `IN PROGRESS`, and `VERIFIED COMPLETE`. A milestone is never declared
> complete merely because code exists.

**M8 makes Syn securely accessible from another device/network via Cloudflare Tunnel (HTTPS) while keeping `llama.cpp` loopback-only.** Existing OpenAI-compatible API, streaming, auth, quotas, observability remain enforced. `llama.cpp` is never exposed; Syn remains `127.0.0.1:8001` and the tunnel dials out.

### M8 scope (in progress)

- **Network boundary:** `Syn 127.0.0.1:8001`, `llama.cpp 127.0.0.1:8080`, `cloudflared` connects locally → `https://<host>` at edge. `Get-NetTCPConnection` audit required. No `0.0.0.0` binding.
- **TLS:** `https://` remote; TLS terminated at Cloudflare; inside host Syn stays `http://127.0.0.1:8001` (documented).
- **Request-size limit:** `SYN_MAX_REQUEST_BODY_BYTES` (default 1 MiB), `413 request_body_too_large` (OpenAI envelope on `/v1/*`), applied to `POST /v1/chat/completions` etc.
- **CORS:** `SYN_CORS_ALLOWED_ORIGINS` (comma-separated, restrictive default empty → no CORS headers, wildcard `*` rejected). Explicit origins only, `allow_credentials=False`.
- **Proxy headers:** M8 deliberately **does not trust** `X-Forwarded-For`/`X-Forwarded-Proto`/`CF-Connecting-IP`; rate limiting remains **identity-based** (`api_key_id`), not IP-based.
- **Auth preserved remotely:** `Bearer` API key required for `/v1/*`, `X-Admin-Secret` for `/admin/*`; tunnel does NOT replace auth.
- **Admin protected:** `/admin/dashboard`, `/admin/observability/*`, `/admin/metrics` remain admin-auth; unauthenticated → 401.
- **Health safe:** `GET /health` public through tunnel exposes only `configured`/`reachable`/`state`/`reason`, no paths/secrets/tracebacks.
- **Error privacy:** remote errors never expose tracebacks, filesystem internals, `Authorization`, tokens.
- **Deployment artifacts:** `deploy/cloudflared.example.yml` (placeholders) + `docs/REMOTE_DEPLOYMENT.md` (Windows commands, SDK examples).
- **Remote verification:** OpenAI SDK `base_url=https://<host>/v1` non-streaming `200` + streaming incremental chunks + `[DONE]`, `X-Request-ID` preserved, quotas brewed remotely, raw `host:8080` unreachable, tunnel stop → remote down / local `http://127.0.0.1:8001` still up.

### M10 scope (in progress)

- `GET /admin/ui` — self-contained admin HTML shell, served without authentication. The operator enters the admin secret in-browser; the secret lives only in JS memory and is sent as `X-Admin-Secret` on subsequent API requests. Secret is never embedded in HTML or persisted to `localStorage`/`sessionStorage`.
- `GET /admin/overview` — unified snapshot: service health, routing mode (configured/passthrough), admission state (active/queued), backend list, request/token/latency/TTFT aggregates. Admin auth required.
- `GET /admin/models` — canonical Syn model IDs from the routing registry. Returns only `id`, `backend_id`, `enabled`, `aliases`; never exposes backend-native paths (GGUF paths). In passthrough mode, returns empty list.
- `GET /admin/backends` — per-backend health: `id`, `type`, `reachable`, `state`, `reason`. Admin auth required.
- `GET /admin/settings` — safe subset of configuration. Never exposes `admin_secret`. File paths are basename-only.
- Separate `ui_router` (`app/api/__init__.py`) carries the public `/admin/ui` route without the `require_admin` dependency; data endpoints remain on the auth-protected `admin_router`.
- All endpoints catch internal errors and return safe envelopes (no tracebacks, no internal paths).
- 60 dedicated tests in `tests/test_admin_m10.py` covering auth, safety, configured/passthrough modes, and backward compatibility.
- Full test suite: **418 passed**.

### M7 scope implemented

- Per-request telemetry fields (all UTC): `queue_wait_ms`, `backend_latency_ms`, `ttft_ms`, `stream_duration_ms`, `total_duration_ms`, `started_at`, `completed_at`
- Outcome taxonomy preserved and exposed: `completed` / `failed` / `cancelled` / `timed_out` / `rejected` — **CANCELLED remains distinct from FAILED**
- Aggregate outcomes and token aggregates (prompt/completion/total + requests_with_unknown_usage)
- Latency aggregates: count/avg/p50/p95/max per dimension; p50/p95 are true percentiles over observed `total_duration_ms`/`ttft_ms` etc.
- Backend health exposed: `configured`/`reachable`/`state`/`reason` via `GET /health` and dashboard; Syn stays alive while backend is unavailable and recovers cleanly
- Admission state exposed: `active`/`queued` vs `max_active`/`max_queue` + `queue_timeout_seconds`
- Recent requests view (max 200, admin-only)
- Client/model breakdowns (requests/completed/failed/cancelled/total_tokens per client/model)
- Admin observability endpoints (all require `X-Admin-Secret` or `Authorization: Bearer <admin-secret>`):
  - `GET /admin/observability/summary` — outcomes + tokens + latency + admission active/queued
  - `GET /admin/observability/latency` — per-dimension latency stats (`total_duration_ms`, `queue_wait_ms`, `backend_latency_ms`, `ttft_ms`, `stream_duration_ms`)
  - `GET /admin/observability/recent?limit=N` — newest-first recent requests
  - `GET /admin/observability/clients` — breakdown by client
  - `GET /admin/observability/models` — breakdown by model
  - `GET /admin/dashboard` — server-rendered HTML, auto-refresh 5s, shows backend/active/queued/completed/failed/cancelled/rejected/tokens/latency/TTFT/recent requests
  - `GET /admin/metrics` — Prometheus-compatible text exposition (`syn_requests_total`, `syn_active_requests`, `syn_queued_requests`, `syn_tokens_total`, `syn_request_duration_seconds`, `syn_ttft_seconds`)
- Percentile semantics: p50 = 50th percentile, p95 = 95th percentile over durable `usage_records`; `max` is observed maximum; `avg` is arithmetic mean
- UTC timestamps throughout (`started_at`/`completed_at` stored as UTC ISO 8601)
- Privacy guarantees: **No prompts or generated responses are stored in observability records. No API keys are exposed. No Authorization headers are logged.** Dashboard/metrics contain only operational IDs (truncated request_id), model names, status, timing, token counts
- No external Prometheus/Grafana/OpenTelemetry deployment is configured yet — the `/admin/metrics` endpoint is ready for scraping but no external collector is bundled
- Local-only deployment status preserved; admin plane remains shared-secret protected
- Single-worker limitation preserved: observability service is in-process and requires `--workers 1` for coherent aggregates

### M6 scope implemented

- Durable `usage_records` table (request_id, user_id, client_id, api_key_id,
  model, streaming, started_at, completed_at, status, prompt_tokens,
  completion_tokens, total_tokens, error_code, **plus M7 latency columns**: `queue_wait_ms`, `backend_latency_ms`, `ttft_ms`, `stream_duration_ms`, `total_duration_ms`)
- Outcomes: `completed`, `failed`, `cancelled`, `timed_out`, `rejected`
- In-process fixed-window-per-minute request rate limiter
  (`SYN_DEFAULT_REQUESTS_PER_MINUTE`)
- Daily request quota (`SYN_DEFAULT_REQUESTS_PER_DAY`) — durable
- Daily token quota (`SYN_DEFAULT_TOKENS_PER_DAY`) — durable,
  **boundary-enforced** (pre-check rejects when `used >= quota`; the
  current request may exceed by one generation)
- Policy inheritance: `key override ?? client value ?? system default`
- Per-client policy management via `PUT /admin/clients/{id}/policy`
- Admin usage inspection: `GET /admin/usage`,
  `GET /admin/usage/clients/{id}`, `GET /admin/usage/keys/{id}`
- OpenAI-compatible error format for `rate_limit_exceeded` (429),
  `request_quota_exceeded` (429), `token_quota_exceeded` (429)
- Privacy: usage records never contain prompts, messages, generated
  content, API keys, or Authorization headers
- Token counts may be NULL for cancelled/failed streams (no fabrication)

### M4 vs M6 distinction

* **M4 (admission)** answers: *How many requests may RUN or WAIT right now?*
* **M6 (usage/quotas)** answers: *How much may this client/key use over time?*

Both are enforced on `/v1/chat/completions`. M4 runs after auth/model
policy; M6 runs after auth, before admission.

### Important: single-process limitation

The in-process rate limiter and observability aggregates are **process-local** and do NOT persist
across restarts in their rate-limiter component, but usage/observability records ARE durable (persisted in
SQLite). Syn must run with `--workers 1` for correct rate-limit and observability semantics.

### M5 scope implemented

- `stream=true` support in `/v1/chat/completions`
- OpenAI-compatible SSE response format (`text/event-stream`)
- Backend streaming abstraction (`AsyncIterator[ChatCompletionChunk]`)
- llama.cpp streaming adapter consuming `httpx.AsyncClient.stream()`
- Incremental SSE parsing tolerant of fragmented transport reads
- OpenAI chunk normalization (`id`, `object=chat.completion.chunk`, `choices[].delta`)
- `[DONE]` sentinel emitted once on normal stream completion
- Admission-slot lifetime spans the entire stream (held, not released early)
- Slot release on: normal completion, backend error, client disconnect,
  task cancellation, unexpected exception
- `asyncio.CancelledError` propagates from client disconnect; generator
  `finally` closes the upstream HTTP response
- Auth and model policy checked BEFORE admission (no capacity wasted)
- Non-streaming path unchanged (M2/M3/M4 regression preserved)
- Real runtime verification with standard OpenAI Python SDK

### Cancellation semantics (precise)

When a client disconnects mid-stream:

1. Starlette/FastAPI cancels the response generator task (`asyncio.CancelledError`).
2. The generator's `finally` block closes the upstream `httpx` streaming response.
3. The admission `async with` context exits, releasing the slot.
4. Any queued request waiting on that slot can now proceed.

What Syn **guarantees**:
- The upstream HTTP connection to llama.cpp is closed.
- The admission slot is released.
- Syn remains alive and can serve new requests.

What Syn does **NOT** claim:
- That llama.cpp will instantly stop GPU generation when the upstream
  connection closes. Runtime observation shows the HTTP request terminates
  and the model may or may not stop generation server-side depending on
  llama.cpp's own behavior. Syn does not control the model process.

### Quick example

```powershell
# 1. Set the admin secret in .env (bootstrap path)
#    SYN_ADMIN_SECRET=<your-bootstrap-secret>

# 2. Bootstrap first credentials via CLI
python -m app.cli create-user --name alice
python -m app.cli create-client --user-id <id> --name huginn
python -m app.cli create-api-key --client-id <id> --name dev-key
# → prints the full API key ONCE (store it securely)

# 3. Start Syn
uvicorn app.main:app --host 127.0.0.1 --port 8001

# 4. Use it
curl http://127.0.0.1:8001/v1/models -H "Authorization: Bearer syn_live_..."
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8001/v1",
    api_key="<your-syn-api-key>",  # Issued by Syn admin/CLI
)

models = client.models.list()
print([m.id for m in models.data])

response = client.chat.completions.create(
    model=models.data[0].id,
    messages=[{"role": "user", "content": "Say the word hello"}],
    temperature=0,
    max_tokens=32,
)
print(response.choices[0].message.content)
```

> M7 is still **local-only**. The admin plane uses a single shared secret
> (NOT a full admin auth system). Do not expose Syn to the internet in M7. Dashboard at `GET /admin/dashboard` and metrics at `GET /admin/metrics` are admin-protected and local-only.

## Roadmap

| Milestone | Focus |
|-----------|-------|
| M0 | Architecture & Service Foundation *(verified complete)* |
| M1 | Private llama.cpp Backend Integration *(verified complete)* |
| M2 | OpenAI Chat Compatibility *(verified complete)* |
| M3 | Users / Clients / API Keys *(verified complete)* |
| M4 | Admission Control / Queue / Concurrency *(verified complete)* |
| M5 | Streaming / Cancellation *(verified complete)* |
| M6 | Usage / Quotas / Rate Limits *(verified complete)* |
| M7 | Observability / Admin Dashboard *(verified complete)* |
| M8  | Secure Remote Deployment *(verified complete)*             |
| M9  | Multi-Model / Multi-Backend Routing *(verified complete)*   |
| M10 | Admin Control Plane UI                                     |

M7 is verified complete. M8 is verified complete. M9 is verified complete. M10 is in progress — adding a self-contained admin UI shell (`/admin/ui`) with auth-protected introspection endpoints (`/admin/overview`, `/admin/models`, `/admin/backends`, `/admin/settings`). See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/ADMIN_UI.md`](docs/ADMIN_UI.md) for the detailed design.
---

## Quick start (local development)

Prerequisites: Python 3.11+ and Git. M0 does **not** require llama.cpp,
a GPU, or internet access.

```powershell
# 1. Create the virtual environment (project-local)
python -m venv .venv

# 2. Activate it
.\.venv\Scripts\Activate.ps1
# (on macOS/Linux: source .venv/bin/activate)

# 3. Install the package in editable mode with dev tools
pip install -e ".[dev]"

# 4. Configure (optional; defaults are safe)
Copy-Item .env.example .env

# 5. Run the gateway
uvicorn app.main:app --host 127.0.0.1 --port 8001

# 6. Verify liveness
Invoke-RestMethod http://127.0.0.1:8001/health
```

### Database / Alembic

SQLite is the M0 database. Migrations are managed with Alembic; the initial
migration is intentionally empty (no tables are required in M0).

```powershell
# Prepare the SQLite database at the configured path
alembic upgrade head
```

The database URL resolves from the application's typed settings
(`SYN_DATABASE_URL` / `.env`), so both the app and Alembic always agree.
(`database/` is `.gitignore`d.)

### Tests

```powershell
pytest
```

The test suite requires **no** running llama.cpp, no GPU, no internet, and no
Cloudflare.

---

## Configuration

Everything is read through typed `pydantic-settings` (`app/config.py`), from
either a local `.env` file or `SYN_`-prefixed environment variables. Safe
development defaults ship in `.env.example`.

| Variable | Default | Description |
|----------|---------|-------------|
| `SYN_APP_NAME` | `Syn` | Application name |
| `SYN_APP_VERSION` | `0.1.0` | Application version |
| `SYN_ENVIRONMENT` | `development` | `development`, `testing`, `production` |
| `SYN_HOST` | `127.0.0.1` | Bind host |
| `SYN_PORT` | `8001` | Bind port |
| `SYN_LOG_LEVEL` | `INFO` | Root log level |
| `SYN_DATABASE_URL` | `sqlite:///./data/syn.db` | SQLAlchemy URL (SQLite in M0) |
| `SYN_BACKEND_TYPE` | `llama_cpp` | Backend identifier |
| `SYN_BACKEND_BASE_URL` | `http://127.0.0.1:8080` | Private llama.cpp address (loopback) |
| `SYN_BACKEND_TIMEOUT_SECONDS` | `120.0` | Backend request timeout |
| `SYN_BACKEND_CONNECT_TIMEOUT_SECONDS` | `10.0` | Per-connection connect timeout |
| `SYN_BACKEND_HEALTH_TIMEOUT_SECONDS` | `5.0` | Backend health-probe timeout |

> M1 implements real connectivity to a private local `llama-server`. The URL
> must remain loopback/private; we never bind, expose, or tunnel it. The
> gateway still boots when the backend is offline and reports it as
> unreachable via `/health`.
---

## Security philosophy

M8 is the first milestone where Syn is **remotely reachable** (via Cloudflare Tunnel HTTPS), but `llama.cpp` **remains loopback-only** (`127.0.0.1:8080`). The data plane (`/v1/*`) still requires a valid Bearer API key, and the management plane (`/admin/*`) is still protected by `SYN_ADMIN_SECRET`. Cloudflare is transport/edge security only; it does NOT replace Syn auth. `llama.cpp` is never exposed; no router port-forward to `8080`.

Documented rules now implemented:

- hashed, high-entropy API keys (M3 — done)
- key revocation and rotation (M3 — done)
- quotas and rate limits (M6 — done, identity-based, not IP-based)
- request-size limits (M8 — done, `SYN_MAX_REQUEST_BODY_BYTES`, 413)
- safe logging — never log prompts, authorization, or secrets (M3 — done)
- restricted CORS (M8 — done, `SYN_CORS_ALLOWED_ORIGINS`, no wildcard)
- admin/user isolation (M7 — done)
- TLS at the network edge (M8 — done, `https://` via Cloudflare, Syn stays `http://127.0.0.1:8001` inside boundary)
- trusted proxy: M8 does NOT trust `X-Forwarded-For`/`CF-Connecting-IP` for security; see `docs/REMOTE_DEPLOYMENT.md`.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), **Security** section, for
the full trust-boundary discussion. Syn makes no enterprise-security claims.

---

## Current limitations (M8)

- **No** tool / function calling, `response_format`, or `logprobs`.
- **No** multi-backend routing (planned for M9).
- The admission controller, rate limiter, and observability aggregates are **single-process** (in-memory + SQLite).
  Running multiple Uvicorn workers would create independent controllers
  and break the global concurrency/rate-limit/observability guarantee. Use `--workers 1`
  (the default).
- The admin plane (including `GET /admin/dashboard` and `GET /admin/metrics`) uses a single shared bootstrap secret (NOT a full admin
  auth system). Do not expose admin over public Internet without additional Cloudflare Access or network controls; `SYN_ADMIN_SECRET` still required remotely.
- The public API is a **deliberate subset** of OpenAI compatibility; Syn is
  not a full clone of the OpenAI API.
- Cancellation guarantee: Syn closes the upstream HTTP connection and
  releases the admission slot. Whether llama.cpp stops generation
  immediately is implementation-dependent and not asserted by Syn.
- Token quota is boundary-enforced: the current request may exceed the
  daily limit by one generation. Syn does not pre-estimate token usage.
- Observability stores no prompts or completions, no API keys, no Authorization headers. **No prompts or generated responses are stored in observability records. No API keys are exposed.** Dashboard/metrics are admin-protected (requires `X-Admin-Secret`).
- **No external Prometheus/Grafana/OpenTelemetry deployment is configured yet.** The `GET /admin/metrics` endpoint exposes Prometheus-compatible text for future scraping but no collector is bundled or auto-configured.
- Backend `GET /health` probes on every call with `SYN_BACKEND_HEALTH_TIMEOUT_SECONDS=5.0`; Syn remains reachable (`200`) during backend outage and recovers when backend returns.
- **Deployment:** `Syn 127.0.0.1:8001` and `llama.cpp 127.0.0.1:8080` must remain loopback-only; remote access is ONLY via Cloudflare Tunnel `https://`. Tunnel failure → remote down, local `http://127.0.0.1:8001` still up. See `docs/REMOTE_DEPLOYMENT.md` for listener audit.

---

## License

MIT