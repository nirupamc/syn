# Syn — Architecture

This document describes the intended high-level architecture of Syn and the
current (M2) state. M2 adds OpenAI-compatible chat inference on top of the M1
backend integration. Anything marked *future* is **not yet implemented**.

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
| **Data plane** | `/v1/*` | OpenAI-compatible inference API for applications (`/v1/models`, `/v1/chat/completions`) |
| **Management plane** | `/admin/*` | Admin operations: health, usage, observability, configuration |

In M2 the data plane is live: `GET /v1/models` and
`POST /v1/chat/completions` (non-streaming) are functional. Management
endpoints currently live under the management path `/health`; `/admin/*` is
future work.

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
| M0 | Architecture & Service Foundation *(complete)* |
| M1 | Private llama.cpp Backend Integration *(complete)* |
| M2 | OpenAI Chat Compatibility *(current)* |
| M3 | Users / Clients / API Keys |
| M4 | Admission Control / Queue / Concurrency |
| M5 | Streaming / Cancellation |
| M6 | Usage / Quotas / Rate Limits |
| M7 | Observability / Admin Dashboard |
| M8 | Secure Remote Deployment |
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

## 11. Future request-state model (not implemented in M1)

```text
RECEIVED → VALIDATING → QUEUED → RUNNING → STREAMING → COMPLETED
                                                      │ FAILED
                                                      │ CANCELLED
                                                      │ TIMED_OUT
REJECTED (at admission)
```

The scheduler and these states are **not** implemented in M1.

---

## 12. Future identity model (documentation only; implemented in M3)

```text
User
   ↓
Client / Project
   ↓
API Key
```

Example:

```text
User
├── Huginn
│   ├── key A
│   └── key B
├── Munin
│   └── key C
└── RAG app
    └── key D
```

M3 will implement this schema.

---

## 13. Current M2 state

M2 is the OpenAI chat compatibility milestone. Syn now exposes a
**deliberate subset** of the OpenAI API surface: `GET /v1/models` and
non-streaming `POST /v1/chat/completions`. Real inference flows from a
standard OpenAI client through Syn to a private local llama.cpp
`llama-server`.

Implemented in M2 (on top of M0/M1):

- `GET /v1/models` via the backend abstraction
- `POST /v1/chat/completions` (non-streaming) via the backend abstraction
- `LlamaCppBackend.chat_completion()` translating the internal typed request
  to llama.cpp's OpenAI-compatible `/v1/chat/completions` endpoint
- Supported request parameters: `model`, `messages`, `temperature`, `top_p`,
  `max_tokens`, `stop`, `stream` (must be `false` or omitted)
- Supported roles: `system`, `user`, `assistant`
- OpenAI-compatible response shape (`id`, `object`, `created`, `model`,
  `choices[].message`, `choices[].finish_reason`, `usage`)
- OpenAI-compatible error format on `/v1/*` (`{"error": {...}}`)
- Explicit rejection of `stream=true` (400 `stream_not_supported`)
- Explicit rejection of unknown models (404 `model_not_found`)
- Backend error mapping to clean 502 responses
- Request IDs propagated to all error responses
- Real runtime verification with the standard OpenAI Python SDK

Not yet implemented (M3+): API authentication, admission control / scheduler,
streaming / cancellation, usage / quotas / rate limits, observability /
dashboard, secure remote deployment, multi-backend routing.