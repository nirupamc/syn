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

Current milestone: **M0 — Architecture & Service Foundation** (this is the
milestone you are looking at).

> Milestone status rules: the only allowed statuses are `NOT STARTED`,
> `IN PROGRESS`, and `VERIFIED COMPLETE`. A milestone is never declared
> complete merely because code exists.

<details>
<summary>M0 acceptance criteria</summary>

1. Git repository is initialized.
2. Python project/environment is valid.
3. Project structure is established.
4. FastAPI application boots.
5. Typed configuration works.
6. `/health` responds over an actual local HTTP request.
7. Database/Alembic foundation is operational.
8. Backend abstraction exists without llama.cpp-specific coupling.
9. Tests exist and all pass.
10. `.gitignore` protects secrets/local artifacts.
11. README exists and reflects actual state.
12. `docs/ARCHITECTURE.md` exists.
13. Future functionality is clearly labeled as future functionality.
14. No M1–M9 functionality has been falsely claimed.
15. Runtime verification was actually performed.

</details>

## Roadmap

| Milestone | Focus |
|-----------|-------|
| M0 | Architecture & Service Foundation |
| M1 | Private llama.cpp Backend Integration |
| M2 | OpenAI Chat Compatibility |
| M3 | Users / Clients / API Keys |
| M4 | Admission Control / Queue / Concurrency |
| M5 | Streaming / Cancellation |
| M6 | Usage / Quotas / Rate Limits |
| M7 | Observability / Admin Dashboard |
| M8 | Secure Remote Deployment |
| M9 | Multi-Model / Multi-Backend Routing |

You are reading the **M0** state. Nothing from M1–M9 has been implemented, and
M0 does **not** proxy real LLM requests yet. See
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
| `SYN_BACKEND_BASE_URL` | `http://127.0.0.1:8080` | Planned llama.cpp address |
| `SYN_BACKEND_TIMEOUT_SECONDS` | `120.0` | Backend request timeout |

> The backend placeholder exists in M0 but performs **no** network I/O. M1
> implements real `llama-server` connectivity, and M0 does not require the
> backend to be running.
---

## Security philosophy

Security is a core design concern from M0 onward, but M0 is **development
only** and is **not** exposed publicly.

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

## Current limitations (M0)

- No real inference requests are proxied.
- **No** `/v1/models` or `/v1/chat/completions` endpoints yet (planned for M2).
- **No** authentication / API keys (planned for M3).
- **No** admission control, queueing, or rate limiting (planned M4/M6).
- **No** streaming / cancellation (planned for M5).
- **No** observability stack / dashboard (planned for M7).
- **No** remote / Tunnel deployment (planned for M8).
- **No** multi-backend routing (planned for M9).
- Backend connectivity itself is **not** implemented (M1).

---

## License

MIT