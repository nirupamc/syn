# Syn — Architecture

This document describes the intended high-level architecture of Syn and the
current (M8) state. M8 makes Syn securely remotely accessible via Cloudflare
Tunnel (HTTPS) while keeping `llama.cpp` loopback-only, preserving auth,
quotas, streaming, observability, and the OpenAI-compatible API.

---

## 1. Overview

Syn is a self-hosted LLM **inference gateway / control plane**. It exposes an
OpenAI-compatible API so applications can treat Syn as a private model
provider, while Syn handles the operational concerns of running inference
backends: authentication, admission control, concurrency, usage accounting,
observability, and model/backend routing.

```text
Clients
   ↓
Syn API
   ↓
Auth / Policy
   ↓
Admission Control
   ↓
Scheduler
   ↓
Backend Abstraction
   ↓
Private llama.cpp
   ↓
GPU
```

The first backend is a local `llama.cpp` `llama-server` exposing an
OpenAI-compatible API on loopback (e.g. `127.0.0.1:8080`). The raw
`llama-server` must **never** be the public interface; it stays private, and
Syn is the only layer that talks to it.

---

## 2. Data plane vs Management plane

| Plane | Path | Purpose |
|-------|------|---------|
| **Data plane** | `/v1/*` | OpenAI-compatible inference API for applications (requires API key) |
| **Management plane** | `/admin/*` | Admin operations: users, clients, API keys (requires admin secret) |

Both planes are live in M3. Health endpoints live under `/health` and remain
unauthenticated (they expose only operational state, not user data).

---

## 3. Trust boundary

```text
PUBLIC / REMOTE
        ↓
TLS / Tunnel / Reverse Proxy
        ↓
Syn Gateway                 ← auth boundary
────────────────────────────
PRIVATE HOST BOUNDARY
        ↓
127.0.0.1:<llama-port>      (loopback only)
        ↓
llama.cpp
        ↓
GPU
```

Documented future rules:

- API authentication (M3)
- hashed, high-entropy API keys (M3)
- key revocation and rotation (M3)
- model permissions (M3)
- quotas and rate limits (M6)
- request-size limits (M4)
- safe logging — never log prompts, auth, or secrets (throughout)
- restricted CORS
- admin/user separation (M7)
- TLS at the network edge (M8)

M1 does **not** expose the application publicly, does not configure any
Cloudflare Tunnel, and does not expose llama.cpp. Syn makes no
enterprise-security claims.

---

## 4. Project layout

```text
app/
    api/        HTTP routes (FastAPI) — health endpoints + /v1/* OpenAI endpoints
    backends/   inference backend abstraction + registry + llama.cpp backend
    core/       cross-cutting: error model, request IDs, logging
    db/         SQLAlchemy engine/session + declarative base (Alembic target)
    logging/    application logging setup
    models/     ORM entities — intentionally empty in M0
    schemas/    Pydantic boundary objects — health schemas + internal chat types
    services/   business logic/orchestration — intentionally thin
tests/
config/
docs/
scripts/
```

Layering: `api → services → backends  ec / models / db`. Code outside the
`backends` package never imports llama.cpp specifics.

---

## 5. Configuration

Typed, validated configuration lives in `app/config.py` via `pydantic-settings`.
Values come from `SYN_`-prefixed environment variables or a local `.env` file.
See the README for the full reference.

M1 defaults for the private local backend:

```text
SYN_BACKEND_TYPE=llama_cpp
SYN_BACKEND_BASE_URL=http://127.0.0.1:8080
SYN_BACKEND_TIMEOUT_SECONDS=120.0
SYN_BACKEND_CONNECT_TIMEOUT_SECONDS=10.0
SYN_BACKEND_HEALTH_TIMEOUT_SECONDS=5.0
```

The backend URL must remain loopback/private. M1 tolerates the backend being
offline at startup: the gateway still boots and reports the backend as
unreachable via `/health`.
---

## 6. Backend contract

`app/backends/base.py` defines the abstract `InferenceBackend` and a
capabilities surface. Config is mapped to a class by `app/backends/registry.py`,
so no code outside the integration layer imports a concrete backend.

In M2 the `LlamaCppBackend` implements `health()`, `models()`, and
`chat_completion()`. `capabilities()` reports the capabilities actually backed
by code (`HEALTH`, `MODELS`, `CHAT_COMPLETIONS`). Remaining lifecycle methods
are still unimplemented:

- `stream_chat_completion()` — streaming (M5)
- `cancel()` — cancellation (M5)

`app/backends/llama_cpp.py` holds the concrete `LlamaCppBackend`, which talks
to a private local `llama-server` over loopback. It owns a single
lifecycle-managed `httpx.AsyncClient`, runs real health probes against
`/health`, discovers models via `/v1/models`, and sends chat completions to
`/v1/chat/completions`. All backend failures are translated into Syn's typed
error model (`BackendUnavailableError`, `BackendTimeoutError`,
`BackendProtocolError`, `BackendInvalidResponseError`) — raw `httpx`
exceptions never escape the backend layer.

---

## 6b. Health semantics (M1)

Health probes run on every `/health` and `/health/ready` request. The probe
result is normalized into one of five states:

| State | Meaning |
|-------|---------|
| `reachable` | `GET /health` returned `200` with `{"status": "ok"}` |
| `unreachable` | connection refused, refused host, or `503` (model not ready) |
| `timeout` | the health probe exceeded the configured health timeout |
| `invalid` | response was malformed or the payload shape was unexpected |
| `unknown` | no probe has run yet (backend not wired) |

**Liveness vs readiness.** `GET /health` always returns `200` while the Syn
process is alive, and reports the backend state honestly in the body.
`GET /ready` reflects only the gateway's own dependencies (the database); an
unavailable inference backend does **not** fail readiness by default, so the
gateway stays observable during backend restarts.

---

## 7. Database foundation

- SQLAlchemy 2.x against SQLite.
- `app/db/base.py` declares `Base` (with a naming convention) as the Alembic
  target metadata.
- `app/db/session.py` is the engine/session holder and the seam for a future
  async engine.
- Alembic reads the configured URL from `SYN_DATABASE_URL`, so the app and
  Alembic never disagree and no URL is hard-coded.
- M0 introduces **no** persistent tables; the initial migration is empty.
  Users / API keys / usage arrive in M3 / M6.

---

## 8. Requests & logging

A middleware (`app/core/request_id.py`) assigns every request a `X-Request-ID`,
accepts an upstream value within strict limits, binds it to the async context,
and echoes it on the response. A filter attaches it to log records. Prompts,
authorization headers, and secrets are **never** logged.

---

## 9. Roadmap & current milestone

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
| M8 | Secure Remote Deployment *(in progress)* |
| M9 | Multi-Model / Multi-Backend Routing |
---

## 10. OpenAI compatibility (M2)

Syn exposes a **deliberate subset** of the OpenAI API surface. It is **not**
a full clone.

### Supported endpoints

| Endpoint | Method | Notes |
|----------|--------|-------|
| `/v1/models` | GET | Lists models discovered from the configured backend |
| `/v1/chat/completions` | POST | Non-streaming only |

### Supported request parameters

| Field | Type | Notes |
|-------|------|-------|
| `model` | string | Must be a model ID returned by `GET /v1/models` |
| `messages` | array | Roles: `system`, `user`, `assistant` |
| `temperature` | float | 0.0 – 2.0 |
| `top_p` | float | 0.0 – 1.0 |
| `max_tokens` | int | ≥ 1 |
| `stop` | array of strings | Optional |
| `stream` | bool | Must be `false` or omitted |

### Supported response fields

The response preserves the OpenAI shape:

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "model-id",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 2,
    "total_tokens": 12
  }
}
```

`usage` is taken directly from the llama.cpp response when available.

### Explicitly unsupported (M2)

- `stream=true` — returns 400 `stream_not_supported`
- `tools` / `tool_choice`
- `functions` / `function_call`
- `response_format`
- `logprobs`
- `n > 1`

These are NOT silently ignored; they are not part of the M2 request schema.
Pydantic validation will reject unknown fields with HTTP 422.

### Error format

Errors are returned in an OpenAI-compatible shape:

```json
{
  "error": {
    "message": "...",
    "type": "invalid_request_error | server_error",
    "param": "...",
    "code": "..."
  },
  "request_id": "..."
}
```

Mapped error codes include:

| Code | HTTP | When |
|------|------|------|
| `validation_error` | 422 | Malformed request / out-of-range parameters |
| `model_not_found` | 404 | Requested model not in backend's model set |
| `stream_not_supported` | 400 | `stream=true` sent |
| `backend_unavailable` | 502 | Backend connection refused / unreachable |
| `backend_timeout` | 502 | Backend exceeded the configured timeout |
| `backend_protocol_error` | 502 | Backend returned a non-200 status |
| `backend_invalid_response` | 502 | Backend returned malformed JSON |

---

## 11. Identity model (M3)

```text
User
   ↓
Client / Project
   ↓
API Key
```

* `User` — ownership/accounting principal. Not a human-login account.
* `Client` — an application/project consuming Syn under a user.
* `ApiKey` — a machine credential bound to a client.

### API key format

```
syn_live_<8-char-public-prefix>_<43-char-secret-suffix>
```

Example: `syn_live_aB3xK9pQ_5CgDB8cRJvfiHRJPOzJEbQI8P_UdLWtkWP3fTzvYHjc`

* The full token is 256 bits of entropy (32 random bytes, url-safe base64).
* Only the SHA-256 hash of the full token is stored.
* The visible prefix (`syn_live_aB3xK9pQ`) is for display and index lookup
  only; it is **not** a secret and is not sufficient for authentication.
* The full secret is returned exactly once at creation/rotation time.

### Hashing and verification

* SHA-256 of the full token → hex digest stored in `api_keys.key_hash`.
* Verification uses `hmac.compare_digest` (constant-time).
* SHA-256 is appropriate here because the token itself has 256 bits of
  entropy. The threat model is not human-password brute force; it is
  database compromise. A leaked DB reveals only hashes, not usable keys.

### Lifecycle

* `created_at` — when the key was issued.
* `expires_at` — optional. After this time the key is rejected (401).
* `revoked_at` — when the key was revoked. Once set, the key is rejected
  immediately (401) on subsequent requests. No restart required.
* `last_used_at` — updated on successful authentication (best-effort).

### Model access policy

Each client has an optional list of `allowed_models`:

* Empty list → all backend models are permitted.
* Non-empty list → only listed models are permitted; others return 403.

`/v1/models` returns only models the authenticated principal is permitted
to use. `/v1/chat/completions` rejects forbidden models with 403.

## 12. Authentication flow

```text
Client
   ↓
Authorization: Bearer <syn_live_...>
   ↓
Syn /v1/*
   ↓
authenticate_request (FastAPI dependency)
   ↓
1. Extract Bearer token
   ↓
2. SHA-256 hash the token
   ↓
3. Look up api_keys by key_hash (indexed)
   ↓
4. Verify constant-time
   ↓
5. Check revoked_at IS NULL
   ↓
6. Check expires_at is NULL or > now
   ↓
7. Update last_used_at
   ↓
8. Load allowed_models for the client
   ↓
9. Return AuthenticatedPrincipal
   ↓
Route handler
```

The `AuthenticatedPrincipal` is a frozen dataclass containing user_id,
client_id, api_key_id, and the allowed_models tuple. It is passed to route
handlers as a typed dependency — no ORM objects cross the request boundary.

## 13. Management plane

Separate from the data plane. All `/admin/*` endpoints require a shared
bootstrap secret sent via `X-Admin-Secret` header or `Authorization: Bearer
<secret>`. This is **not** a full admin auth system — there are no admin
user accounts. The single shared secret is acceptable for local development
and the bootstrap path.

```text
POST   /admin/users               create user
GET    /admin/users               list users
POST   /admin/clients             create client (with optional allowed_models)
GET    /admin/clients             list clients
POST   /admin/api-keys            create API key (returns full secret ONCE)
GET    /admin/api-keys            list API keys (metadata only)
POST   /admin/api-keys/{id}/revoke  revoke a key
POST   /admin/api-keys/{id}/rotate  create replacement + revoke old
```

**Inference API keys are NOT valid for admin operations.** Admin auth is
entirely separate.

### Bootstrap path

The very first user/client/key cannot be created via the API (chicken-and-
egg). The documented bootstrap path is the CLI:

```powershell
python -m app.cli create-user --name alice
python -m app.cli create-client --user-id <id> --name huginn
python -m app.cli create-api-key --client-id <id> --name dev
```

The CLI runs directly against the configured database. It is the only way
to create the initial credentials without already having a key.

## 14. Safe logging

* Full API keys are never logged.
* Key hashes are never logged.
* Authorization headers are never logged.
* Safe log fields: `request_id`, `api_key_id`, `client_id`, `key_prefix`,
  `auth outcome`.

## 15. Admission control (M4)

Syn's M4 scheduler is an **admission controller**, not an inference
scheduler. It decides only whether a request is allowed to reach the
backend now, whether it waits in a bounded queue, or whether it is
rejected. llama.cpp owns actual inference scheduling and continuous
batching internally.

```text
authenticated request
   ↓
validation / policy
   ↓
AdmissionController
   ├── running (max_active_requests)
   ├── queued (max_queue_size, FIFO)
   └── rejected (queue_full / queue_timeout)
   ↓
backend (llama.cpp)
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SYN_MAX_ACTIVE_REQUESTS` | `1` | Max concurrent chat completions |
| `SYN_MAX_QUEUE_SIZE` | `8` | Max waiting requests |
| `SYN_QUEUE_TIMEOUT_SECONDS` | `30.0` | Max time a request may wait |

### Properties

* **At most `max_active_requests`** requests may execute concurrently.
* **At most `max_queue_size`** requests may wait in the FIFO queue.
* **A queued request** that waits longer than `queue_timeout_seconds` is
  rejected with `503 queue_timeout`.
* **A request that finds the queue full** is rejected immediately with
  `429 queue_full`.
* **FIFO** queue discipline. No priority, no per-client shares.
* **Slots are released** on success, backend error, timeout, cancellation,
  or any unexpected exception (`async with` / try/finally semantics).
* **State is in-memory only.** On restart, active/queued requests are lost.
* **Auth and model policy are checked BEFORE admission.** Invalid keys and
  forbidden models never occupy queue capacity.
* **Single-process.** Running multiple Uvicorn workers would create
  independent admission controllers and violate the global concurrency
  guarantee. Syn must run with `--workers 1` (the default).

### Distinction from rate limiting

M4 answers: *"How many can execute/wait RIGHT NOW?"*
M6 (future) will answer: *"How much may this client use OVER TIME?"*

These are separate concerns. M4 is not rate limiting.

### Status visibility

`GET /admin/status` (admin auth required) returns:

```json
{
  "admission": {
    "active": 1,
    "max_active": 2,
    "queued": 3,
    "max_queue": 8,
    "queue_timeout_seconds": 30.0
  }
}
```

No prompts, no API keys, no per-request content.

### Relationship with llama.cpp

llama.cpp already implements token-level scheduling and may use continuous
batching. Syn's admission controller limits the *number of concurrent HTTP
requests* sent to llama.cpp. It does NOT try to coordinate with llama.cpp's
internal scheduler. The `max_active_requests` value is operator
configuration, not dynamically inferred from GPU state.

## 16. Streaming (M5)

Syn supports OpenAI-compatible streaming chat completions. A client sets
`stream: true` and receives Server-Sent Events.

```text
authenticated request (stream=true)
   ↓
admission (slot acquired)
   ↓
backend stream opens (httpx.AsyncClient.stream)
   ↓
SSE chunks forwarded incrementally
   ↓
[normal] [DONE] sentinel → slot released
[client disconnect] upstream closed → slot released
[backend error] stream terminated → slot released
[unexpected exception] stream terminated → slot released
```

### SSE format

```text
data: {"id":"chatcmpl-...","object":"chat.completion.chunk",...}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk",...}

data: [DONE]
```

Content-Type: `text/event-stream`

### Backend contract

`InferenceBackend.stream_chat_completion(request)` returns an
`AsyncIterator[ChatCompletionChunk]`. The backend is responsible for
consuming its upstream transport incrementally and for cleanly closing
resources when the iterator is closed (e.g. on client disconnect).

### SSE parsing

Syn includes a dedicated SSE parser (`app.core.sse`) that:

* buffers across chunk boundaries (a single SSE event may be split
  across multiple transport reads)
* handles CRLF and LF line endings
* ignores comment lines (`: ...`)
* handles the `[DONE]` sentinel

### Chunk normalization

Upstream chunks are normalized into `ChatCompletionChunk`:

* `id` — preserved from upstream
* `object` — forced to `chat.completion.chunk`
* `created` — preserved from upstream
* `model` — preserved from upstream
* `choices[].index` — preserved
* `choices[].delta.role` — first chunk only
* `choices[].delta.content` — incremental content
* `choices[].finish_reason` — `stop`/`length`/etc. on final chunk

### Admission-slot lifetime (critical)

The admission slot is acquired **before** the `StreamingResponse` is
created and released **only when** the streaming generator completes,
the client disconnects, or an error occurs. This is implemented via:

```python
async def stream_generator():
    async with admission.acquire():
        async for chunk in backend.stream_chat_completion(req):
            yield _format_sse(chunk)
        yield _format_sse_done()
```

The `async with` ensures the slot is released when the generator exits,
regardless of how (normal completion, exception, or client disconnect).

### Cancellation semantics (precise)

When a client disconnects:

1. Starlette/FastAPI cancels the response generator task
   (`asyncio.CancelledError`).
2. The generator's `finally` block closes the upstream `httpx` response.
3. The `async with admission.acquire()` context exits, releasing the slot.
4. Any queued request can now proceed.

**What Syn guarantees:**
* The upstream HTTP connection to llama.cpp is closed.
* The admission slot is released.
* Syn remains alive and can serve new requests.

**What Syn does NOT claim:**
* That llama.cpp will instantly stop GPU generation when the upstream
  connection closes. Whether generation continues server-side is
  implementation-dependent on llama.cpp's own behavior. Syn does not
  control the model process.

### Timeout policy for streaming

Streaming requests use a different timeout policy than non-streaming:

* **Connect timeout** — short (e.g. 10s), same as non-streaming
* **Overall request timeout** — `None` (no fixed limit; the consumer's
  request lifetime is the upper bound)
* **No per-chunk idle timeout** — chunk arrival is the natural liveness
  signal; long pauses between tokens are valid for slow generations

### Non-streaming compatibility

`stream: false` (or omitted) continues to return the full JSON response
exactly as in M2/M3/M4. The streaming and non-streaming code paths
share normalization helpers but do not share the response generation
path.

## 17. Usage tracking, rate limits, and quotas (M6)

M6 adds durable usage records, per-key/client rate limits, and daily
quotas. The flow is:

```text
authenticated request
  ↓
rate/quota pre-check (after auth/model policy, before admission)
  ↓
admission
  ↓
backend
  ↓
usage accounting (in finally block)
  ↓
persistent usage_records
```

### UsageRecord schema (M6 + M7 extensions)

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | Primary key |
| `request_id` | string | Correlation ID (from M0) |
| `user_id`, `client_id`, `api_key_id` | UUID | Foreign keys (nullable, SET NULL on delete) |
| `model` | string | Model ID |
| `streaming` | int | 0/1 |
| `started_at`, `completed_at` | datetime | UTC — all timestamps UTC |
| `status` | string | `completed` / `failed` / `cancelled` / `timed_out` / `rejected` — **CANCELLED distinct from FAILED** |
| `prompt_tokens`, `completion_tokens`, `total_tokens` | int? | Nullable when not reliably known (e.g. cancelled streams) |
| `queue_wait_ms` | int? | Time spent waiting in admission queue (ms) — M7 |
| `backend_latency_ms` | int? | Backend inference latency (ms) — M7 |
| `ttft_ms` | int? | Time to first token for streaming (ms) — M7 |
| `stream_duration_ms` | int? | Streaming duration after first token (ms) — M7 |
| `total_duration_ms` | int? | End-to-end request duration (ms) — M7 |
| `error_code` | string? | Backend error code on failure |

**Privacy invariant:** Usage records never contain prompt text, messages,
generated content, API keys, or Authorization headers.

### Outcomes

* `completed` — successful response (non-streaming or stream completed
  normally)
* `failed` — backend error / timeout / protocol failure
* `cancelled` — client disconnected mid-stream
* `timed_out` — queue timeout (admission never reached backend)
* `rejected` — queue full (admission never reached backend)

### Rate-limit algorithm

Process-local fixed-window per-minute rate limiter. The window is computed
as `now - (now % 60)`. State is `dict[key, (window_start, count)]` guarded
by an `asyncio.Lock`.

* **Not durable** across restarts. Daily quotas are durable.
* **Process-local.** Use `--workers 1`.
* **Per-key.** The rate limiter key is `f"rpm:{api_key.id}"`.

### Quota semantics

* **Request quota** counts requests that pass the precheck and reach
  admission. Rejected (queue_full) and timed-out (queue_timeout) requests
  do NOT count. Failed/cancelled requests DO count.
* **Token quota** is **boundary-enforced**: the precheck rejects when
  `used_tokens_today >= daily_token_quota`. The current request may
  exceed by one generation. Syn does not pre-estimate token usage.
* **Window:** UTC calendar day. Resets at 00:00 UTC.
* **Counts:** only records with non-null `total_tokens` are summed.
  Unknown/cancelled records are NOT counted as zero.

### Policy inheritance

```
key override ?? client value ?? system default
```

* `0` (or negative) at any level means **unlimited** and short-circuits
  remaining levels.
* `None` at any level falls through to the next.

### Error codes

| Code | HTTP | When |
|------|------|------|
| `rate_limit_exceeded` | 429 | Rate limit hit |
| `request_quota_exceeded` | 429 | Daily request quota hit |
| `token_quota_exceeded` | 429 | Daily token quota hit (boundary) |

All use the OpenAI-compatible error envelope on `/v1/*`.

### Restart durability

| State | Durable? | Notes |
|-------|----------|-------|
| `usage_records` rows | **Yes** | Persisted in SQLite |
| Daily request quota counters | **Yes** | Computed from usage_records |
| Daily token quota counters | **Yes** | Computed from usage_records |
| In-process rate limiter state | **No** | Resets on restart |

This means daily quotas survive restarts, but the per-minute rate limit
window is reset.

## 18. Observability (M7)

M7 adds **per-request telemetry**, **aggregate observability**, and an
**admin dashboard/metrics surface** on top of the durable usage_records.

### Per-request telemetry

Every `/v1/chat/completions` request (streaming and non-streaming) records:

| Field | Meaning |
|-------|---------|
| `queue_wait_ms` | Time waiting in admission FIFO before acquiring active slot |
| `backend_latency_ms` | Time from backend request start to response/stream start |
| `ttft_ms` | Streaming only: time to first token (first chunk arrival) |
| `stream_duration_ms` | Streaming only: duration after first token until `[DONE]`/close |
| `total_duration_ms` | End-to-end wall time `completed_at - started_at` (ms) |

All timestamps are UTC (`started_at`, `completed_at` as ISO 8601 UTC).
Telemetry is written in a `finally` block so failed/cancelled/timed_out
requests are still recorded.

### Aggregates and percentile semantics

Aggregates are computed over the durable `usage_records` table:

* **Outcomes:** `completed` / `failed` / `cancelled` / `timed_out` / `rejected` with counts — `cancelled` remains distinct from `failed`
* **Tokens:** `prompt_tokens`, `completion_tokens`, `total_tokens` summed; `requests_with_unknown_usage` tracks records where tokens were NULL
* **Latency:** for each dimension (`total_duration_ms`, `queue_wait_ms`, `backend_latency_ms`, `ttft_ms`, `stream_duration_ms`) the service computes `count`, `avg_ms`, `p50_ms`, `p95_ms`, `max_ms` — `p50`/`p95` are true percentiles over observed values (sorted), not approximations
* **Admission visibility:** `active`/`queued` live gauges from the `AdmissionController` vs `max_active`/`max_queue`

### Backend health / outage behavior

`GET /health` always returns `200` while Syn is alive. The body includes a live probe:

```json
{"backend": {"configured": true, "reachable": true, "state": "reachable", "reason": "healthy"}}
{"backend": {"configured": true, "reachable": false, "state": "unreachable", "reason": "unreachable: connection failed"}}
```

During a backend outage (`SYN_BACKEND_BASE_URL` → dead host) Syn:
* remains alive (`/health` 200, `/admin/*` 200),
* exposes `reachable=false` / `state=unreachable`,
* returns clean `502 backend_unavailable` with OpenAI-compatible envelope for inference attempts,
* stays observable and **recovers** when the backend returns (`reachable=true`, inference `200`).

The dashboard and `/health` probe on every call with `SYN_BACKEND_HEALTH_TIMEOUT_SECONDS` (5s).

### Admin observability surface (all require admin secret)

| Endpoint | Purpose |
|----------|---------|
| `GET /admin/observability/summary` | Outcomes + tokens + latency (p50/p95) + admission active/queued |
| `GET /admin/observability/latency` | Per-dimension latency stats |
| `GET /admin/observability/recent?limit=N` | Newest-first recent requests (max 200), truncates request_id |
| `GET /admin/observability/clients` | Breakdown by client |
| `GET /admin/observability/models` | Breakdown by model |
| `GET /admin/dashboard` | Server-rendered HTML (auto-refresh 5s), shows backend/active/queued/completed/failed/cancelled/rejected/tokens/latency/TTFT/recent requests |
| `GET /admin/metrics` | Prometheus-compatible text: `syn_requests_total`, `syn_active_requests`, `syn_queued_requests`, `syn_tokens_total`, `syn_request_duration_seconds`, `syn_ttft_seconds` |

All admin endpoints require `X-Admin-Secret: <secret>` or `Authorization: Bearer <secret>`; inference keys (`syn_live_*`) are rejected (403/401).

### Privacy guarantees

* **No prompts or generated responses are stored in observability records.**
* **No API keys are exposed.** Only truncated `request_id`, model name, status, timing, token counts.
* No `Authorization` headers or raw secrets ever appear in dashboard/metrics/observability payloads.
* No external Prometheus/Grafana/OpenTelemetry deployment is configured yet — `/admin/metrics` is scrape-ready but no collector is bundled.

### Local-only and single-worker

Observability is local-only and in-process. Like admission/rate limiting, the `ObservabilityService` is **single-process** and requires `--workers 1` for coherent aggregates. The admin plane (including dashboard/metrics) is not hardened for public internet.

### Flow

```text
authenticated request
  ↓
usage precheck → admission (records queue_wait_ms)
  ↓
backend (records backend_latency_ms, ttft_ms, stream_duration_ms)
  ↓
finally: record total_duration_ms + status → usage_records
  ↓
admin: summary / latency / recent / breakdowns / dashboard / metrics
```

## 19. Secure Remote Deployment (M8)

M8 makes Syn **remotely reachable via Cloudflare Tunnel (HTTPS)** while keeping
`llama.cpp` **loopback-only**. All M0–M7 guarantees (OpenAI API, streaming,
auth, quotas, observability) still hold remotely.

### Trust boundary (M8)

```text
REMOTE CLIENT (https://)
        │
        ▼
Cloudflare Edge (TLS termination)
        │
        │ outbound-only tunnel
        ▼
cloudflared ──► Syn 127.0.0.1:8001 ──► llama.cpp 127.0.0.1:8080 ──► GPU
        ▲              ▲                     ▲
        │              │                     │
     Internet      loopback-only       loopback-only (never 0.0.0.0)
```

* **Cloudflare Tunnel** (`cloudflared`) on the Syn host dials out to Cloudflare;
  no inbound firewall rule for `8001`/`8080` is required.
* **TLS:** `https://<host>` at the edge; inside host boundary Syn stays
  `http://127.0.0.1:8001` (documented accurately). Do not expose plaintext HTTP publicly.
* **Config outside repo:** `deploy/cloudflared.example.yml` contains placeholders
  (`tunnel: <TUNNEL_ID>`, `credentials-file: <OUTSIDE_REPO_PATH>`); real
  `cloudflared.yml`/`*.json` credentials live outside, gitignored.
* **Named tunnel preferred** for long-lived SSE; Quick Tunnel is not the final M8 deployment.

### Request-size limit (M8)

* `SYN_MAX_REQUEST_BODY_BYTES` (default `1048576` = 1 MiB), validated in `app/config.py:192`.
  Prevents unbounded buffering while allowing normal chat completions (few KB).
* `app/core/request_size.py` middleware checks `Content-Length` against limit.
  Exceeds → `413` (`RequestBodyTooLargeError` `code=request_body_too_large`):
  ```json
  {"error":{"message":"request body too large: …","type":"invalid_request_error","code":"request_body_too_large"},"request_id":"…"}
  ```
  Protects at least `POST /v1/chat/completions`.

### CORS (M8)

* `SYN_CORS_ALLOWED_ORIGINS` (comma-separated, default `""` → restrictive, no CORS headers).
  `*` is rejected in config (`app/config.py:201`).
* `app/main.py:124` adds `CORSMiddleware` only if origins configured, with
  `allow_credentials=False`, `allow_methods=["GET","POST","PUT","DELETE","OPTIONS"]`,
  `allow_headers=["Authorization","Content-Type","X-Request-ID","X-Admin-Secret"]`.
  No wildcard with credentials. Browser clients set explicit origin; server-to-server needs no CORS.

### Trusted proxy headers (M8)

* M8 **does not trust** `X-Forwarded-For`, `X-Forwarded-Proto`, `CF-Connecting-IP`
  for security decisions (`app/main.py:144` comment).
* Rate limiting remains **identity-based** (`api_key_id`, `RateLimiter` in `app/core/rate_limit.py`),
  not IP-based. Cloudflare is transport; Syn still enforces `Bearer` auth,
  model permissions, quotas, admission.

### Admin surface (M8)

* All `/admin/*` (`/admin/dashboard`, `/admin/observability/*`, `/admin/metrics`)
  still require `require_admin` (`app/core/admin_auth.py:35`) — `X-Admin-Secret` or
  `Authorization: Bearer <admin_secret>`. Inference keys rejected. Health `/health`
  remains public but exposes only safe `backend.configured/reachable/state/reason`.

### Error privacy (M8)

* Remote errors never expose Python tracebacks, filesystem paths, `Authorization`,
  tokens, or Cloudflare credentials. `SynError` envelope only (`app/main.py:142`).

### Deployment artifacts (M8)

* `deploy/cloudflared.example.yml` — safe example with placeholders.
* `docs/REMOTE_DEPLOYMENT.md` — Windows-first guide: prerequisites, loopback starts,
  tunnel create/run, remote SDK (OpenAI `base_url=https://…/v1`), streaming,
  negative tests (no key 401, oversized 413, raw `host:8080` unreachable),
  observability through tunnel, listener audit.

### Known M8 limitations

* Single-process only (`--workers 1`).
* Admin is still shared-secret (not full RBAC); tunnel does NOT add OAuth/SSO.
* No M9 multi-model/multi-backend routing, no load balancing, no Redis/K8s.

## 20. Current M8 state (in progress)

M8 is the secure-remote-deployment milestone. Syn is being made remotely reachable
via Cloudflare Tunnel while keeping `llama.cpp` loopback-only.

Implemented so far in M8 (on top of M0–M7):

- `SYN_MAX_REQUEST_BODY_BYTES` + `RequestSizeLimitMiddleware` (413)
- `SYN_CORS_ALLOWED_ORIGINS` + `CORSMiddleware` (restrictive default)
- `deploy/cloudflared.example.yml` + `docs/REMOTE_DEPLOYMENT.md`
- Updated `app/config.py`, `app/main.py`, `app/core/errors.py`, `.env.example`, `.gitignore`
- Proxy-header policy documented (no trust, identity-based limits)

Pending for VERIFIED COMPLETE: remote runtime verification A–I through
`https://` tunnel (health, auth, non-streaming, streaming incremental,
policy/limit, observability, raw backend isolation, listener audit,
tunnel-stop test) on a real second device.

Not yet implemented (M9): multi-model/multi-backend routing. **No M9 functionality is added in M8.**