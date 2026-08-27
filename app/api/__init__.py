"""API package.

M0 exposes only health endpoints on the management plane path. The
OpenAI-compatible data plane (``/v1/*``) is implemented in M2.

Planned future separation:

    DATA PLANE        /v1/*
    MANAGEMENT PLANE  /admin/*
    (health currently under the management path /health)
"""

from app.api.chat import router as chat_router
from app.api.health import router as health_router

__all__ = ["health_router", "chat_router"]