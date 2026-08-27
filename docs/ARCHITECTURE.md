# Syn — Architecture

This document describes the intended high-level architecture of Syn and the
current (M1) state. M1 implements private llama.cpp backend integration on top
of the M0 service foundation. Anything marked *future* is **not yet implemented**.

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
| **Data plane** | `/v1/*` | OpenAI-compatible inference API for applications (`/v1/models`, `/v1/chat/completions`, ...) |
| **Management plane** | `/admin/*` | Admin operations: health, usage, observability, configuration |

This is a **future architectural boundary**. In M1 only `health`-family
endpoints are exposed under the management path; `/v1/*` data-plane endpoints
are still reserved for M2 (documented in the OpenAI compatibility plan below).

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
    api/        HTTP routes (FastAPI) — M1: health endpoints
    backends/   inference backend abstraction + registry + llama.cpp backend
    core/       cross-cutting: error model, request IDs, logging
    db/         SQLAlchemy engine/session + declarative base (Alembic target)
    logging/    application logging setup
    models/     ORM entities — intentionally empty in M0
    schemas/    Pydantic boundary objects — health schemas in M1
    services/   business logic/orchestration — intentionally thin in M0
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

In M1 `health()` and `models()` are implemented for the llama.cpp backend.
`capabilities()` reports only the capabilities actually backed by code
(`HEALTH`, `MODELS`). Remaining lifecycle methods are still unimplemented:

- `chat_completion()` — single completion (M2)
- `stream_chat_completion()` — streaming (M5)
- `cancel()` — cancellation (M5)

`app/backends/llama_cpp.py` holds the concrete `LlamaCppBackend`, which talks
to a private local `llama-server` over loopback. It owns a single
lifecycle-managed `httpx.AsyncClient`, runs real health probes against
`/health`, and discovers models via `/v1/models`. All backend failures are
translated into Syn's typed error model (`BackendUnavailableError`,
`BackendTimeoutError`, `BackendProtocolError`, `BackendInvalidResponseError`)
or, for health probes, into a safe `BackendHealthResult` — raw `httpx`
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
| M1 | Private llama.cpp Backend Integration *(current)* |
| M2 | OpenAI Chat Compatibility |
| M3 | Users / Clients / API Keys |
| M4 | Admission Control / Queue / Concurrency |
| M5 | Streaming / Cancellation |
| M6 | Usage / Quotas / Rate Limits |
| M7 | Observability / Admin Dashboard |
| M8 | Secure Remote Deployment |
| M9 | Multi-Model / Multi-Backend Routing |
---

## 10. OpenAI compatibility plan *(documented, NOT yet implemented)*

Planned V1 support (targeting M2):

| Endpoint | Method |
|----------|--------|
| `/v1/models` | GET |
| `/v1/chat/completions` | POST |

Both non-streaming and streaming chat are eventually planned (streaming is M5).
Initial planned request parameters: `model`, `messages`, `temperature`,
`top_p`, `max_tokens`, `stop`, `stream`.

Potential future / conditional parameters: `seed`, `response_format`, `tools`,
`tool_choice`. Unsupported parameters must eventually **fail explicitly**
rather than be silently ignored.

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

## 13. Current M1 state

M1 is the private llama.cpp backend integration milestone. It does **not**
proxy real LLM requests to clients yet.

Implemented:

- typed configuration
- FastAPI application factory with lifespan
- `/health`, `/health/liveness`, `/health/ready`
- request-ID middleware and logging policy
- error model (including backend-specific errors)
- SQLAlchemy + Alembic foundation (empty migration)
- backend abstraction + registry + concrete `LlamaCppBackend`
- real backend health probing (`/health`) with normalized health states
- real backend model discovery (`/v1/models`) into Syn-owned typed models
- distinct backend timeouts (request / connect / health)
- clean mapping of backend failures into Syn's error model
- startup tolerance when llama.cpp is offline
- automated tests (including `httpx.MockTransport` isolation tests)

Not yet implemented (M2+): `/v1/*` chat completions, auth / API keys,
admission control / scheduler, streaming / cancellation, usage / quotas / rate
limits, observability / dashboard, secure remote deployment, multi-backend
routing.