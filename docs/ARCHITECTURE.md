# Syn — Architecture

This document describes the intended high-level architecture of Syn and the
current (M3) state. M3 adds user/client/API-key authentication, a
management plane, and model access policy on top of the M2 data plane.

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
| M0 | Architecture & Service Foundation *(complete)* |
| M1 | Private llama.cpp Backend Integration *(complete)* |
| M2 | OpenAI Chat Compatibility *(complete)* |
| M3 | Users / Clients / API Keys *(current)* |
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

## 15. Current M3 state

M3 is the authentication milestone. Syn now requires a valid API key for
all `/v1/*` endpoints and exposes a separate management plane for
credentials.

Implemented in M3 (on top of M0/M1/M2):

- `User`, `Client`, `ApiKey`, `ClientAllowedModel` ORM models
- Alembic migration `0002_m3_auth` (users, clients, api_keys, allowed_models)
- API key generation (256-bit entropy), SHA-256 hashing, constant-time verify
- `AuthenticatedPrincipal` typed request context
- Bearer token authentication dependency on `/v1/*`
- 401 responses for missing/malformed/invalid/revoked/expired keys
- Model access policy per-client (allow-list)
- `/v1/models` filtered by principal's permissions
- `/v1/chat/completions` enforces model access (403 for forbidden)
- Management plane `/admin/*` with bootstrap secret
- CLI bootstrap commands (`create-user`, `create-client`, `create-api-key`,
  `revoke-api-key`)
- Immediate revocation (no restart)
- Rotation: new key created, old key revoked (configurable)
- Secret-safe logging
- OpenAI-compatible error format for auth errors
- Real runtime verification with standard OpenAI Python SDK

Not yet implemented (M4+): admission control / scheduler, streaming /
cancellation, usage / quotas / rate limits, observability / dashboard,
secure remote deployment, multi-backend routing.