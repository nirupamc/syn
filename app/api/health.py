"""Health endpoints (management plane).

    GET /health           liveness of the Syn gateway itself
    GET /health/liveness  alias for liveness
    GET /health/ready     readiness of the gateway's own dependencies

Health semantics (M1):

* ``/health`` always returns 200 while the gateway process is alive. The body
  includes a *real* backend health probe result (reachable/unreachable/...).

* ``/health/ready`` reports whether the gateway's own internal dependencies
  (currently the database) are operative. Decision: an unavailable inference
  backend does NOT fail readiness by default; it is reported in the body so the
  gateway stays observable during backend restarts. A change of this policy is
  a deliberate production concern, not an accident.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import Settings, get_settings
from app.core.request_id import get_request_id
from app.schemas.health import (
    BackendHealth,
    HealthResponse,
    ReadinessResponse,
)

router = APIRouter(tags=["health"])


async def _backend_health(request: Request) -> BackendHealth:
    """Run a real health probe against the configured backend (if any).

    Never raises: probe failures are mapped into a typed, safe BackendHealth.
    """
    backend = getattr(request.app.state, "backend", None)
    if backend is None:
        return BackendHealth(
            configured=True,
            reachable=False,
            state="unknown",
            reason="backend not wired during startup",
        )
    try:
        result = await backend.health()
    except Exception as exc:  # noqa: BLE001 - health must never crash the API
        return BackendHealth(
            configured=True,
            reachable=False,
            state="unreachable",
            reason=f"health probe raised: {type(exc).__name__}",
        )
    return BackendHealth(
        configured=result.configured,
        reachable=result.reachable,
        state=result.state.value,
        reason=result.reason,
        server_version=result.server_version,
        model=result.model,
    )


@router.get("/health", response_model=HealthResponse)
@router.get("/health/liveness", response_model=HealthResponse)
async def liveness(request: Request) -> HealthResponse:
    """Prove the Syn gateway process is alive."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    backend = await _backend_health(request)
    return HealthResponse(
        status="ok",
        service="syn",
        version=settings.app_version,
        environment=settings.environment.value,
        backend=backend,
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

    backend = await _backend_health(request)
    response = ReadinessResponse(
        status="ready" if database_ok else "not_ready",
        database="ok" if database_ok else "error",
        backend=backend,
        request_id=get_request_id(),
    )
    status_code = 200 if database_ok else 503
    return JSONResponse(status_code=status_code, content=response.model_dump())