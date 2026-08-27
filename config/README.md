# config/

Runtime / static configuration that is not Python environment config. In M0
the application is configured via `app/config.py` + `.env` / `SYN_*` env vars,
so this directory is intentionally mostly empty. It exists as a stable home
for future non-Python Static config (e.g. declared model aliases, routing
manifest) that increases as routing (M9) and deployment (M8) arrive.

See `.env.example` at the repo root for local overrides.