"""Health endpoint response schemas.

These are simple, typed, and testable. No OpenAI-style wrapping is needed for
health responses; those are management-plane concerns (later milestones).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class BackendHealth(BaseModel):
    """Reported status of the configured inference backend.

    During M0 backend connectivity is NOT implemented (M1). We expose the
    configured backend but never claim it is reachable.
    """

    configured: bool = True
    reachable: bool = False
    reason: str = "backend connectivity is not implemented until M1"


class HealthResponse(BaseModel):
    """Response for ``GET /health`` (liveness)."""

    status: Literal["ok", "degraded"] = "ok"
    service: str = "syn"
    version: str
    environment: str
    backend: BackendHealth
    request_id: Optional[str] = Field(default=None)


class ReadinessResponse(BaseModel):
    """Response for ``GET /health/ready``.

    Readiness reflects whether the gateway's own dependencies are operating.
    It intentionally does not claim backend readiness (backend wiring is M1).
    """

    status: Literal["ready", "not_ready"] = "ready"
    database: Literal["ok", "error"]
    backend: BackendHealth
    request_id: Optional[str] = Field(default=None)