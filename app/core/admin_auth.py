"""Admin plane authentication (M3).

The admin plane is protected by a separate bootstrap secret loaded from
``SYN_ADMIN_SECRET``. This is deliberately NOT a full admin auth system:
there are no admin user accounts, no sessions, no OAuth. The single shared
secret is acceptable for local development and the bootstrap path.

The secret is read from typed configuration (NOT from raw env). If the
secret is empty, the admin plane is closed (returns 401).

Inference API keys are NOT valid for admin operations. Admin auth is
entirely separate.
"""

from __future__ import annotations

import hmac

from fastapi import Header, Request

from app.core.errors import AdminAuthError
from app.logging import get_logger

logger = get_logger("syn.admin")


def _configured_admin_secret(request: Request) -> str:
    """Read the configured admin secret from app state."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return ""
    return getattr(settings, "admin_secret", "") or ""


def require_admin(
    request: Request,
    x_admin_secret: str | None = Header(default=None, alias="X-Admin-Secret"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """FastAPI dependency: require a valid admin secret.

    Accepts the secret via either:
      * ``X-Admin-Secret: <secret>`` header
      * ``Authorization: Bearer <secret>`` header

    The constant-time comparison guards against timing side-channels.
    """
    expected = _configured_admin_secret(request)
    if not expected:
        logger.warning("admin request rejected: admin secret not configured")
        raise AdminAuthError("admin plane is not configured")

    provided: str | None = None
    if x_admin_secret:
        provided = x_admin_secret
    elif authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided = parts[1].strip()

    if not provided:
        raise AdminAuthError("admin authentication required")

    if not hmac.compare_digest(provided, expected):
        logger.warning("admin request rejected: invalid secret")
        raise AdminAuthError("invalid admin credentials")
