"""Authentication dependency for /v1/* (M3).

Validates a Bearer token from the ``Authorization`` header, looks up the key
by hash, verifies it is not revoked/expired, and returns an
``AuthenticatedPrincipal`` for downstream handlers.

Safe logging: only the key prefix and IDs are ever logged. The full token and
the key hash are NEVER logged.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import api_keys
from app.core.errors import (
    AuthenticationError,
    ExpiredApiKeyError,
    InvalidApiKeyError,
    RevokedApiKeyError,
)
from app.core.principal import AuthenticatedPrincipal
from app.db import Database
from app.logging import get_logger
from app.models.api_key import ApiKey
from app.models.client import Client
from app.models.client_allowed_model import ClientAllowedModel
from app.models.user import User

logger = get_logger("syn.auth")


def _get_db_session(request: Request) -> Session:
    """Get a SQLAlchemy session from the app's Database.

    The session is created per-request and closed when the request ends.
    Tests can override by setting ``app.state.database`` to a Database bound
    to an in-memory SQLite engine.
    """
    db: Optional[Database] = getattr(request.app.state, "database", None)
    if db is None or db.session_factory is None:
        raise AuthenticationError(
            "database unavailable for authentication",
            code="authentication_required",
        )
    return db.session_factory()


def _principal_from_api_key(api_key: ApiKey) -> AuthenticatedPrincipal:
    """Build an AuthenticatedPrincipal from a validated ApiKey ORM row."""
    client: Client = api_key.client
    user: User = client.user
    allowed = _load_allowed_models(api_key.client_id)
    return AuthenticatedPrincipal(
        user_id=user.id,
        user_name=user.name,
        client_id=client.id,
        client_name=client.name,
        api_key_id=api_key.id,
        api_key_prefix=api_key.key_prefix,
        allowed_models=allowed,
    )


def _load_allowed_models(client_id: str) -> Optional[tuple[str, ...]]:
    """Return the tuple of allowed model IDs for a client, or None for unrestricted."""
    # We need a session here; the caller is expected to be within a request
    # that has an active session. We import here to avoid a circular import.
    from app.db import Database as _DB  # noqa: F401

    # The session is passed via the closure of the auth dependency; we use a
    # simple approach: query via the global session factory if available.
    # In practice the auth dependency passes the session to us.
    raise NotImplementedError("Use _principal_from_api_key_with_session instead")


def _principal_from_api_key_with_session(
    api_key: ApiKey, session: Session
) -> AuthenticatedPrincipal:
    """Build an AuthenticatedPrincipal with model access policy loaded."""
    client: Client = api_key.client
    user: User = client.user
    rows = (
        session.query(ClientAllowedModel)
        .filter(ClientAllowedModel.client_id == client.id)
        .all()
    )
    allowed: Optional[tuple[str, ...]] = None
    if rows:
        allowed = tuple(r.model_id for r in rows)
    return AuthenticatedPrincipal(
        user_id=user.id,
        user_name=user.name,
        client_id=client.id,
        client_name=client.name,
        api_key_id=api_key.id,
        api_key_prefix=api_key.key_prefix,
        allowed_models=allowed,
    )


def authenticate_request(
    request: Request,
) -> AuthenticatedPrincipal:
    """FastAPI dependency: validate Bearer token, return principal.

    This is the lightweight dependency. For endpoints that also need the
    underlying ApiKey/Client ORM rows (e.g. for usage/quota tracking),
    use ``authenticate_with_orm`` instead.
    """
    session = _get_db_session(request)
    try:
        auth_header = request.headers.get("Authorization")
        token = _extract_bearer_token(auth_header)

        # Hash the token and look up by hash.
        token_hash = api_keys.hash_api_key(token)
        api_key = (
            session.query(ApiKey)
            .filter(ApiKey.key_hash == token_hash)
            .one_or_none()
        )
        if api_key is None:
            logger.info(
                "auth failure: no matching key (prefix=%s)",
                _safe_prefix(token),
            )
            raise InvalidApiKeyError("invalid API key")

        # Verify hash (defense in depth; the lookup already did the comparison
        # via the indexed column, but a constant-time check guards against
        # hash collisions in the DB).
        if not api_keys.verify_api_key(token, api_key.key_hash):
            logger.info("auth failure: hash mismatch (api_key_id=%s)", api_key.id)
            raise InvalidApiKeyError("invalid API key")

        now = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)

        if api_key.revoked_at is not None:
            logger.info(
                "auth failure: revoked key (api_key_id=%s, prefix=%s)",
                api_key.id,
                api_key.key_prefix,
            )
            raise RevokedApiKeyError("API key has been revoked")

        if api_key.expires_at is not None and api_key.expires_at <= now:
            logger.info(
                "auth failure: expired key (api_key_id=%s, prefix=%s)",
                api_key.id,
                api_key.key_prefix,
            )
            raise ExpiredApiKeyError("API key has expired")

        # Update last_used_at (best-effort; do not fail auth on error).
        try:
            api_key.last_used_at = now
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()

        principal = _principal_from_api_key_with_session(api_key, session)
        logger.debug(
            "auth ok (api_key_id=%s, client_id=%s, prefix=%s)",
            principal.api_key_id,
            principal.client_id,
            principal.api_key_prefix,
        )
        return principal
    finally:
        session.close()


def authenticate_with_orm(
    request: Request,
) -> tuple[AuthenticatedPrincipal, ApiKey, Client]:
    """FastAPI dependency that returns the principal plus the ApiKey and
    Client ORM rows. Used by endpoints that need ORM access (usage/quota
    tracking, admin operations).

    The session is closed when the returned objects go out of scope; in
    practice the route handler will use the same session for any
    further DB operations within the same request.

    Note: the ORM objects are bound to the session that was used to
    load them. The session is closed at the end of the request, so the
    caller MUST not hold these objects beyond the request lifecycle.
    For long-lived references, copy the needed fields out.
    """
    session = _get_db_session(request)
    try:
        auth_header = request.headers.get("Authorization")
        token = _extract_bearer_token(auth_header)

        token_hash = api_keys.hash_api_key(token)
        api_key = (
            session.query(ApiKey)
            .filter(ApiKey.key_hash == token_hash)
            .one_or_none()
        )
        if api_key is None:
            logger.info(
                "auth failure: no matching key (prefix=%s)",
                _safe_prefix(token),
            )
            raise InvalidApiKeyError("invalid API key")

        if not api_keys.verify_api_key(token, api_key.key_hash):
            logger.info("auth failure: hash mismatch (api_key_id=%s)", api_key.id)
            raise InvalidApiKeyError("invalid API key")

        now = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)

        if api_key.revoked_at is not None:
            logger.info(
                "auth failure: revoked key (api_key_id=%s, prefix=%s)",
                api_key.id,
                api_key.key_prefix,
            )
            raise RevokedApiKeyError("API key has been revoked")

        if api_key.expires_at is not None and api_key.expires_at <= now:
            logger.info(
                "auth failure: expired key (api_key_id=%s, prefix=%s)",
                api_key.id,
                api_key.key_prefix,
            )
            raise ExpiredApiKeyError("API key has expired")

        # Update last_used_at (best-effort).
        try:
            api_key.last_used_at = now
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()

        # Eagerly load the client relationship.
        client = api_key.client

        principal = _principal_from_api_key_with_session(api_key, session)
        logger.debug(
            "auth ok (api_key_id=%s, client_id=%s, prefix=%s)",
            principal.api_key_id,
            principal.client_id,
            principal.api_key_prefix,
        )
        # Detach the objects from the session so they can be used after
        # the session is closed. The caller should not modify them.
        session.expunge(api_key)
        session.expunge(client)
        return principal, api_key, client
    finally:
        session.close()


def _extract_bearer_token(authorization_header: Optional[str]) -> str:
    """Pull the token out of an Authorization header. Raises on missing/malformed."""
    if not authorization_header:
        raise AuthenticationError(
            "missing Authorization header",
            code="authentication_required",
        )
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationError(
            "Authorization header must be: Bearer <api_key>",
            code="authentication_required",
        )
    token = parts[1].strip()
    if not token or not api_keys.is_valid_format(token):
        raise InvalidApiKeyError("invalid API key format")
    return token


def _safe_prefix(token: str) -> str:
    """Return a safe-to-log prefix of a token, or '<malformed>'."""
    try:
        return api_keys.visible_prefix(token)
    except Exception:  # noqa: BLE001
        return "<malformed>"


# Re-export the dependency for FastAPI
AuthenticatedPrincipalDep = Depends(authenticate_request)
