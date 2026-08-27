"""Pydantic schemas.

M0 only defines API response/health schemas. Request DTOs for OpenAI-compatible
endpoints will be introduced in M2. This package exists now so the API and
service layers have a clear home for typed boundary objects.
"""

from app.schemas.health import (
    BackendHealth,
    HealthResponse,
    ReadinessResponse,
)

__all__ = ["HealthResponse", "ReadinessResponse", "BackendHealth"]