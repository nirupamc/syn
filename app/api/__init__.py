"""API package.

Syn exposes two planes:

    DATA PLANE        /v1/*      (M2 — OpenAI-compatible, requires API key)
    MANAGEMENT PLANE  /admin/*   (M3 — bootstrap-secret protected)
    HEALTH            /health    (M0 — management, unauthenticated)
"""

from app.api.admin import router as admin_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router

__all__ = ["health_router", "chat_router", "admin_router"]