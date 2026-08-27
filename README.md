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

Current milestone: **M5 — Streaming / Cancellation** *(verified)*.

> Milestone status rules: the only allowed statuses are `NOT STARTED`,
> `IN PROGRESS`, and `VERIFIED COMPLETE`. A milestone is never declared
> complete merely because code exists.

**M5 adds OpenAI-compatible streaming chat completions.** Standard
OpenAI clients can now use `stream=True` to receive Server-Sent Events.
Streaming requests participate in the same admission queue as non-streaming
requests, holding an active slot for the entire stream lifetime. Client
disconnects cleanly close the upstream HTTP connection and release the slot.

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

> M3 is still **local-only**. The admin plane uses a single shared secret
> (NOT a full admin auth system). Do not expose Syn to the internet in M3.

## Roadmap

| Milestone | Focus |
|-----------|-------|
| M0 | Architecture & Service Foundation *(complete)* |
| M1 | Private llama.cpp Backend Integration *(complete)* |
| M2 | OpenAI Chat Compatibility *(complete)* |
| M3 | Users / Clients / API Keys *(complete)* |
| M4 | Admission Control / Queue / Concurrency *(complete)* |
| M5 | Streaming / Cancellation *(this milestone)* |
| M6 | Usage / Quotas / Rate Limits |
| M7 | Observability / Admin Dashboard |
| M8 | Secure Remote Deployment |
| M9 | Multi-Model / Multi-Backend Routing |

Nothing from M6–M9 is implemented yet. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the detailed design.
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

M3 is **local-only**. The data plane (`/v1/*`) requires a valid Bearer API
key, and the management plane (`/admin/*`) is protected by a separate
bootstrap secret. There is no rate limiting, no quota, and no public-internet
safety. **Do not expose Syn to the internet in M3.** It binds to loopback
by default and assumes a trusted local environment.

Documented future rules (to be implemented in later milestones):

- hashed, high-entropy API keys (M3 — done)
- key revocation and rotation (M3 — done)
- quotas and rate limits (M6)
- request-size limits
- safe logging — never log prompts, authorization, or secrets (M3 — done)
- restricted CORS
- admin/user isolation (M7)
- TLS at the network edge (M8)

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), **Security** section, for
the full trust-boundary discussion. Syn makes no enterprise-security claims.

---

## Current limitations (M5)

- **No** tool / function calling, `response_format`, or `logprobs`.
- **No** usage accounting or quotas (planned M6).
- **No** per-client rate limiting (planned M6).
- **No** observability stack / dashboard (planned for M7).
- **No** remote / Tunnel deployment (planned for M8).
- **No** multi-backend routing (planned for M9).
- The admission controller is **single-process**. Running multiple Uvicorn
  workers would create independent controllers and break the global
  concurrency guarantee. Use `--workers 1` (the default).
- The admin plane uses a single shared bootstrap secret (NOT a full admin
  auth system). This is acceptable for local development only.
- The public API is a **deliberate subset** of OpenAI compatibility; Syn is
  not a full clone of the OpenAI API.
- Cancellation guarantee: Syn closes the upstream HTTP connection and
  releases the admission slot. Whether llama.cpp stops generation
  immediately is implementation-dependent and not asserted by Syn.

---

## License

MIT