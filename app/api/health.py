"""Health endpoints (management plane).

    GET /health          liveness of the Syn gateway itself
    GET /health/live     alias for liveness
    GET /health/ready    readiness of the gateway's own dependencies

Readiness checks only the gateway's own dependencies (currently: database). It
does NOT claim backend readiness; backend connectivity is implemented in M1.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import Settings, get_settings
from app.core.errors import ConfigurationError
from app.core.request_id import get_request_id
from app.schemas.health import (
    BackendHealth,
    HealthResponse,
    ReadinessResponse,
)

router = APIRouter(tags=["health"])


def _backend_health(settings: Settings) -> BackendHealth:
    """Describe the configured backend without claiming reachability."""
    return BackendHealth(
        configured=True,
        reachable=False,
        reason="backend connectivity is not implemented until M1",
    )


@router.get("/health", response_model=HealthResponse)
@router.get("/health/liveness", response_model=HealthResponse)
async def liveness(request: Request) -> HealthResponse:
    """Prove the Syn gateway process is alive."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    return HealthResponse(
        status="ok",
        service="syn",
        version=settings.app_version,
        environment=settings.environment.value,
        backend=_backend_health(settings),
        request_id=get_request_id(),
    )


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(request: Request) -> JSONResponse:
    """Gateway readiness based on its own dependencies (not the backend)."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    db = getattr(request.app.state, "database", None)
    database_ok = True
    if db is not None and db.engine is not None:
        try:
            with db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - readiness must not crash
            database_ok = False
    else:
        database_ok = False

    response = ReadinessResponse(
        status="ready" if database_ok else "not_ready",
        database="ok" if database_ok else "error",
        backend=_backend_health(settings),
        request_id=get_request_id(),
    )
    status_code = 200 if database_ok else 503
    return JSONResponse(status_code=status_code, content=response.model_dump())