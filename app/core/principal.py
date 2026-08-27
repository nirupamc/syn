"""Authenticated principal context (M3).

A typed, immutable record of who is making a request, derived from a valid
API key. This is what authenticated request handlers receive — NOT ORM
objects. Keeping it small avoids passing live SQLAlchemy sessions through
the request lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The authenticated identity behind a request.

    * ``allowed_models`` is None when the client has no restrictions
      (all models permitted). It is a tuple of model IDs when restricted.
    """

    user_id: str
    user_name: str
    client_id: str
    client_name: str
    api_key_id: str
    api_key_prefix: str
    allowed_models: tuple[str, ...] | None = field(default=None)

    def can_use_model(self, model_id: str) -> bool:
        """Return True if this principal may use the given model."""
        if self.allowed_models is None:
            return True
        return model_id in self.allowed_models
