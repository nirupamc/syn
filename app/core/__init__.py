"""Core cross-cutting helpers for Syn."""

from app.core.errors import (
    BackendUnavailableError,
    NotFoundError,
    SynError,
    ValidationError as SynValidationError,
)

__all__ = [
    "SynError",
    "BackendUnavailableError",
    "NotFoundError",
    "SynValidationError",
]