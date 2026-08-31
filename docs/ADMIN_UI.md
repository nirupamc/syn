# Syn Admin UI (M10)

The admin UI is a self-contained HTML shell served at `GET /admin/ui`. It
provides a read-only control plane for inspecting the gateway's runtime
state — models, backends, settings, and observability aggregates — without
exposing any secret material.

## Architecture

```
Browser (operator)
    |
    | GET /admin/ui  (no auth — serves static HTML + inline JS)
    |
    v
Syn gateway (admin UI shell)
    |
    | GET /admin/overview  (X-Admin-Secret)
    | GET /admin/models    (X-Admin-Secret)
    | GET /admin/backends  (X-Admin-Secret)
    | GET /admin/settings  (X-Admin-Secret)
    v
RoutingService + ObservabilityService + AdmissionController
```

## Authentication model

The UI shell itself is **unauthenticated** — it returns a minimal HTML page
and a JavaScript auth overlay. The operator enters the admin secret in the
browser; the secret lives only in JS memory for the lifetime of the page
session and is sent as the `X-Admin-Secret` header on subsequent API
requests. The secret is:

- **Never** embedded in the served HTML.
- **Never** persisted to `localStorage` or `sessionStorage`.
- **Never** written to disk or cookies.

Data endpoints (`/admin/overview`, `/admin/models`, `/admin/backends`,
`/admin/settings`) all require `X-Admin-Secret` or `Authorization: Bearer
<admin-secret>`. Inference API keys are rejected (403).

## Endpoints

### `GET /admin/ui`

Serves the admin UI HTML shell. No authentication required — the page is a
thin shell; all data is fetched client-side from auth-protected endpoints
after the operator enters the admin secret.

### `GET /admin/overview`

Returns a unified snapshot of gateway health, routing mode, admission state,
backend list, and request/token/latency aggregates.

**Response** (`OverviewOut`):

| Field | Description |
|-------|-------------|
| `syn_healthy` | `true` when the observability service is available |
| `routing_configured` | `true` when running in configured (multi-backend) mode |
| `routing_mode` | `"configured"` or `"passthrough"` |
| `admission` | active/queued counts, max values, queue timeout |
| `backends` | list of `BackendListItem` (id, type, reachable, state, reason) |
| `requests` | completed/failed/cancelled/timed_out/rejected counts |
| `tokens` | prompt/completion/total token aggregates |
| `latency_ms` | avg/p50/p95 for total request latency |
| `ttft_ms` | avg/p50/p95 for time-to-first-token |

### `GET /admin/models`

Returns canonical Syn model IDs from the routing registry. In configured mode,
returns models from `config/routing.json`. In passthrough mode, returns an
empty list (models are discovered dynamically from the backend).

**Response** (`ModelsListOut`):

| Field | Description |
|-------|-------------|
| `configured` | `true` when in configured mode |
| `models` | list of `{id, backend_id, enabled, aliases}` — no backend-native paths |

### `GET /admin/backends`

Returns the health state of each configured backend.

**Response** (`BackendsListOut`):

| Field | Description |
|-------|-------------|
| `configured` | `true` when in configured mode |
| `backends` | list of `{id, type, reachable, state, reason}` |

### `GET /admin/settings`

Returns a safe subset of gateway configuration. The `admin_secret` is never
exposed. File paths are basename-only.

**Response** (`SettingsOut`):

| Field | Description |
|-------|-------------|
| `backend_type` | Configured backend type |
| `cors_origins` | List of allowed CORS origins |
| `queue_timeout_seconds` | Admission queue timeout |
| `max_active_requests` | Admission max concurrent requests |
| `max_queue_size` | Admission max queue depth |
| `max_request_body_bytes` | Request body size limit |
| `routing_file_path` | Basename of routing config (or `"(passthrough)"`) |

## Security guarantees

1. **No secret leakage**: Admin secret never appears in UI HTML, JS source, or
   API responses from `/admin/settings`.
2. **No path leakage**: Filesystem paths are stripped to basenames in settings.
   GGUF/model paths never appear in `/admin/models` or `/admin/backends`.
3. **No prompt/response leakage**: Observability endpoints never return
   prompts, messages, or generated content.
4. **Auth-gated data**: All data endpoints require `X-Admin-Secret`. The UI
   shell itself is public (serves HTML only).
5. **Inference key rejection**: Bearer API keys (inference keys) are rejected
   from all `/admin/*` data endpoints with 403.
6. **Safe error rendering**: All endpoints catch internal errors and return
   safe envelopes — no Python tracebacks, no internal paths, no stack traces.
