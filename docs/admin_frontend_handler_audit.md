# Admin frontend handler audit

Captured before the native Syn UI rewrite on 2026-09-02.

## Authentication and shared behavior

- `adminSecret` is assigned only from `#secret-input` and retained in JavaScript memory.
- `api(path, init)` sends `X-Admin-Secret` to the existing `/admin/*` contract and parses JSON responses.
- `showStatus(message, type)` renders non-browser status feedback.
- `switchSection(id)` removes `.active` from all ten sibling sections and activates one target.
- `SECTION_LOADERS` refreshes a section from the backend when its navigation item is selected.
- The theme handler switches `data-theme`; the old implementation did not persist it.

## Data loaders and actions

- Overview: `loadOverview()` -> `GET /admin/overview`.
- Users: `loadUsers()` -> `GET /admin/users`; create used `POST /admin/users` via `prompt()`.
- Clients: `loadClients()` -> `GET /admin/clients`; create used `POST /admin/clients` via two `prompt()` calls and omitted the required user selection.
- API keys: `loadKeys()` -> `GET /admin/api-keys`; create -> `POST /admin/api-keys`; rotate -> `POST /admin/api-keys/{id}/rotate?revoke_old=true`; revoke -> `POST /admin/api-keys/{id}/revoke`.
- One-time key display: `showPlaintextOnce()` holds the token in `plaintextKey`; `clearPlaintext()` clears both memory and DOM text on close/navigation.
- Models: `loadModels()` -> `GET /admin/models`.
- Backends: `loadBackends()` -> `GET /admin/backends`.
- Routing: `loadRouting()` -> `POST /admin/routing/preview`.
- Usage: `loadUsage()` -> `GET /admin/usage`.
- Observability: `loadObservability()` -> `GET /admin/observability/recent?limit=20`; `renderObservability()` applies model and status filters in memory.
- Settings: `loadSettings()` -> `GET /admin/settings`.

## Rewrite requirements derived from the audit

- Preserve all endpoint paths and the in-memory secret/key lifecycle.
- Replace user and client prompts with native Syn dialogs; client creation must select a real owner.
- Keep API-key create, rotate, revoke, copy-once, dismiss, Escape, and navigation-clear behavior.
- Join user/client/model collections in memory only to render readable names and backend/model relationships.
- Escape all backend-provided values before inserting generated markup.
