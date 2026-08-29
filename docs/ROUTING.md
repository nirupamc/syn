# Syn — Multi-Model / Multi-Backend Routing (M9)

## Overview

M9 introduces deterministic model-to-backend routing via a configuration file.
When `config/routing.json` is present, Syn operates in **configured mode**,
mapping public model IDs (and their aliases) to specific backends. When the
file is absent, Syn falls back to **passthrough mode** with single-backend
behavior (M0–M8).

## Configuration (`config/routing.json`)

The routing config file is placed at `config/routing.json`. Its format:

```json
{
  "backends": [
    {"id": "backend-a", "type": "llama_cpp", "base_url": "http://127.0.0.1:8080"},
    {"id": "backend-b", "type": "llama_cpp", "base_url": "http://127.0.0.1:8081"}
  ],
  "models": [
    {"id": "model-a", "backend_id": "backend-a", "backend_model": "model-a-native", "aliases": ["alias-a"]},
    {"id": "model-b", "backend_id": "backend-b", "backend_model": "model-b-native", "aliases": ["alias-b"]}
  ]
}
```

- **`backends`**: List of configured backends with `id`, `type`, and `base_url`.
- **`models`**: List of model entries. Each has:
  - `id`: The public canonical model ID clients request.
  - `backend_id`: Which backend serves this model.
  - `backend_model`: The model name visible to the backend (may differ from the public ID).
  - `aliases`: Optional client-facing aliases that resolve to this canonical ID.

## Routing Modes

### Passthrough mode (no `config/routing.json`)

- Single default backend handles all requests.
- `/v1/models` returns models from the default backend.
- `/v1/chat/completions` routes to the default backend.
- All M0–M8 behavior applies (auth, quotas, streaming, observability).
- `/admin/routing/preview` and `/admin/observability/backends` are not available.

### Configured mode (`config/routing.json` exists)

- Models are routed to their assigned backends based on the config.
- `/v1/models` returns only models visible to the authenticated principal (respecting `allowed_models`).
- `/v1/chat/completions` routes to the correct backend and returns the **canonical** model ID in the response.
- Backend-native model paths never leak client-facing.
- `/admin/routing/preview` shows routing decisions without executing them.
- `/admin/observability/backends` returns per-backend breakdown.

## Key Endpoints

### `/v1/models` [GET]

Lists models visible to the authenticated principal.

- In **configured mode**: Returns only models the principal's `allowed_models` permits.
- In **passthrough mode**: Returns models from the single default backend.

### `/v1/chat/completions` [POST]

Create a chat completion. The `model` field determines routing:

- **Canonical model ID** (e.g. `"model-a"`): Routes to the assigned backend.
- **Alias** (e.g. `"alias-a"`): Resolves to the canonical model ID first, then routes.
- **Backend-native path** (e.g. `"model-a-native.gguf"`): Rejected — clients must use the public canonical ID.

Response always uses the **canonical model ID**, not the backend-native path.

### `/admin/routing/preview` [POST]

Shows routing decision for a model without executing the request.

**Request**:

```json
{"model": "model-a"}
```
or
```json
{"model": "alias-a"}
```

**Response** (admin auth required via `X-Admin-Secret`):

```json
{
  "requested_model": "alias-a",
  "canonical_model": "model-a",
  "backend_id": "backend-a",
  "reason": "alias_match"
}
```

- Inference API keys are rejected (401/403).
- Unknown models return 400 with explanation.
- Alias resolution is shown, preventing canonical bypass.

### `/admin/observability/backends` [GET]

Returns per-backend breakdown for observability:

```json
[
  {"backend_id": "backend-a", "requests": 42, "errors": 1},
  {"backend_id": "backend-b", "requests": 13, "errors": 0}
]
```

- Admin auth required.
- Inference keys rejected.

### `/health` [GET]

Returns aggregate backend health:

```json
{
  "status": "ok",
  "backend": {
    "configured": true,
    "reachable": true,
    "state": "degraded",
    "reason": "unreachable backends: backend-b"
  }
}
```

- Public endpoint (no auth required).
- Reports reachability across all configured backends.

### `/admin/status` [GET]

Returns routing mode and per-backend health (admin auth required):

```json
{
  "routing": {
    "mode": "configured",
    "backend_a": {"reachable": true, "state": "reachable"},
    "backend_b": {"reachable": false, "state": "unreachable"}
  }
}
```

- Admin auth required via `X-Admin-Secret`.

## Alias Resolution & Policy

1. Client sends `model: "alias-a"`.
2. Router resolves `alias-a` → `model-a` (canonical).
3. Canonical model `model-a` is checked against the principal's `allowed_models`.
4. If allowed → routing proceeds to `backend-a`.
5. If denied → 403 `model_not_found` / `403 Forbidden`.

**Crucially**: alias resolution happens **before** authorization. An alias cannot bypass model policy — it only resolves to what its canonical model allows.

## No Silent Fallback

If a backend is unreachable (e.g. `backend-b` down):

- Requests to `model-b` → `502 backend_unavailable`.
- Requests to `model-a` → succeed (routed to `backend-a`).
- No traffic is silently redirected to another backend.
- Observability records the `backend_id` for the request that succeeded/failed.

## Usage Record Backend Attribution

Each `usage_record` stores the `backend_id` that served the request, enabling:

- Per-backend request counts in observability dashboards.
- Backend-specific latency/timing analysis.
- Failure attribution (which backend caused a `502`).
- No prompt/content leakage — only the `backend_id` UUID is stored.

## Mode Switching

- Remove or delete `config/routing.json` → immediately reverts to passthrough mode on next restart.
- Add `config/routing.json` → configured mode on next restart.
- No runtime mode switching — the presence of the config file determines the mode.

## Privacy Guarantees

- **Canonical model IDs only** in client responses and usage records.
- **Backend-native paths** (GGUF filenames, internal model names) never leak.
- **`backend_id`** in usage records is a UUID, not a human-readable path.
- No prompt text, generated content, or API keys in observability records.

## Security

- `/admin/routing/preview` requires `X-Admin-Secret` or `Authorization: Bearer <admin_secret>`.
- Inference API keys (`syn_live_*`) are rejected from admin endpoints.
- Model policy (`allowed_models`) is enforced after alias resolution — aliases cannot bypass restrictions.
- No silent fallback between backends.