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

Current milestone: **M2 — OpenAI Chat Compatibility** *(verified)*.

> Milestone status rules: the only allowed statuses are `NOT STARTED`,
> `IN PROGRESS`, and `VERIFIED COMPLETE`. A milestone is never declared
> complete merely because code exists.

**M2 makes Syn a working OpenAI-compatible inference endpoint for the first
time.** A standard OpenAI client (or `curl`) can list models and request
non-streaming chat completions. Requests flow through the backend abstraction
to a private local llama.cpp `llama-server`. No authentication yet.

### M2 scope implemented

- `GET  /v1/models` — list models discovered from the configured backend
- `POST /v1/chat/completions` — non-streaming chat completions
- OpenAI-compatible request fields: `model`, `messages`, `temperature`, `top_p`,
  `max_tokens`, `stop`, `stream` (must be `false` or omitted)
- OpenAI-compatible roles: `system`, `user`, `assistant`
- OpenAI-compatible response shape (`id`, `object`, `created`, `model`,
  `choices[].message`, `choices[].finish_reason`, `usage`)
- OpenAI-compatible error format (`{"error": {"message", "type", "param", "code"}}`)
- Explicit failure for `stream=true` (returns 400)
- Explicit failure for unknown models (returns 404)
- Backend errors mapped to clean 502 responses (not crash / traceback)
- Real runtime verification: standard OpenAI Python SDK works against Syn
- All M0/M1 regression tests still pass

### Quick example

```powershell
# 1. Start Syn (with llama.cpp running on 127.0.0.1:8080)
uvicorn app.main:app --host 127.0.0.1 --port 8001

# 2. List models
curl http://127.0.0.1:8001/v1/models

# 3. Chat completion
curl -X POST http://127.0.0.1:8001/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d '{
    "model": "models\gemma-4-E2B-it-Q4_K_M.gguf",
    "messages": [{"role": "user", "content": "Reply with exactly: SYN_OK"}],
    "temperature": 0,
    "max_tokens": 32
  }'
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8001/v1",
    api_key="m2-no-auth-placeholder",  # Auth is M3; any value is accepted.
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

> M2 is **local-only and unauthenticated**. Do not expose it to the internet.
> See "Security" below.

Helpers/mocked requests are used for isolated tests; no real GPU needed for
the test suite.

## Roadmap

| Milestone | Focus |
|-----------|-------|
| M0 | Architecture & Service Foundation *(complete)* |
| M1 | Private llama.cpp Backend Integration *(complete)* |
| M2 | OpenAI Chat Compatibility *(this milestone)* |
| M3 | Users / Clients / API Keys |
| M4 | Admission Control / Queue / Concurrency |
| M5 | Streaming / Cancellation |
| M6 | Usage / Quotas / Rate Limits |
| M7 | Observability / Admin Dashboard |
| M8 | Secure Remote Deployment |
| M9 | Multi-Model / Multi-Backend Routing |

Nothing from M3–M9 is implemented yet. See
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

M2 is **local-only and unauthenticated**. There is no API authentication, no
rate limiting, and no authorization. **Do not expose Syn to the internet in
M2.** It binds to loopback by default and assumes a trusted local environment.

Documented future rules (to be implemented in later milestones):

- API authentication (M3)
- hashed, high-entropy API keys (M3)
- key revocation and rotation (M3)
- model permissions (M3)
- quotas and rate limits (M6)
- request-size limits
- safe logging — never log prompts, authorization, or secrets
- restricted CORS
- admin/user isolation
- TLS at the network edge (M8)

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), **Security** section, for
the full trust-boundary discussion. Syn makes no enterprise-security claims.

---

## Current limitations (M2)

- **No** authentication / API keys (planned for M3). M2 accepts any value for
  `Authorization: Bearer` and does not validate it.
- **No** streaming (`stream=true` is explicitly rejected with 400).
- **No** tool / function calling, `response_format`, or `logprobs`.
- **No** admission control, queueing, concurrency limits, or rate limiting
  (planned M4/M6).
- **No** usage accounting or quotas (planned M6).
- **No** observability stack / dashboard (planned for M7).
- **No** remote / Tunnel deployment (planned for M8).
- **No** multi-backend routing (planned for M9).
- The public API is a **deliberate subset** of OpenAI compatibility; Syn is
  not a full clone of the OpenAI API.

---

## License

MIT