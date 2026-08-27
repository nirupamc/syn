"""API package.

M0 exposes only health endpoints on the management plane path. The
OpenAI-compatible data plane (``/v1/*``) is documented but deliberately NOT
implemented until M2.

Planned future separation:

    DATA PLANE        /v1/*
    MANAGEMENT PLANE  /admin/*
    (health currently under the management path /health)
"""

from app.api.health import router as health_router

__all__ = ["health_router"]