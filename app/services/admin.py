"""Admin service layer (M3).

Business logic for the management plane. All persistence is done through
SQLAlchemy sessions. The service functions are deliberately small and
typed; they do NOT expose ORM objects directly to the API layer.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from sqlalchemy.orm import Session

from app.core import api_keys
from app.core.errors import (
    NotFoundError,
    SynError,
    ValidationError,
)
from app.models.api_key import ApiKey
from app.models.client import Client
from app.models.client_allowed_model import ClientAllowedModel
from app.models.user import User


# ---- users ------------------------------------------------------------------


def create_user(session: Session, name: str) -> User:
    name = name.strip()
    if not name:
        raise ValidationError("name cannot be empty", code="validation_error")
    existing = session.query(User).filter(User.name == name).one_or_none()
    if existing is not None:
        raise SynError(
            f"user '{name}' already exists",
            code="user_exists",
            http_status=409,
        )
    user = User(name=name)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def list_users(session: Session) -> list[User]:
    return session.query(User).order_by(User.created_at).all()


def get_user(session: Session, user_id: str) -> User:
    user = session.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise NotFoundError(f"user '{user_id}' not found", code="user_not_found")
    return user


# ---- clients ----------------------------------------------------------------


def _set_allowed_models(
    session: Session, client: Client, allowed: Optional[list[str]]
) -> None:
    # Replace existing rows with the new set (or clear them).
    session.query(ClientAllowedModel).filter(
        ClientAllowedModel.client_id == client.id
    ).delete()
    if allowed:
        for mid in allowed:
            session.add(ClientAllowedModel(client_id=client.id, model_id=mid))


def get_client_allowed_models(session: Session, client_id: str) -> list[str]:
    rows = (
        session.query(ClientAllowedModel)
        .filter(ClientAllowedModel.client_id == client_id)
        .all()
    )
    return [r.model_id for r in rows]


def create_client(
    session: Session,
    user_id: str,
    name: str,
    description: Optional[str] = None,
    allowed_models: Optional[list[str]] = None,
) -> Client:
    name = name.strip()
    if not name:
        raise ValidationError("name cannot be empty", code="validation_error")
    user = get_user(session, user_id)
    client = Client(user_id=user.id, name=name, description=description)
    session.add(client)
    session.flush()  # need client.id for allowed_models rows
    _set_allowed_models(session, client, allowed_models)
    session.commit()
    session.refresh(client)
    return client


def list_clients(session: Session, user_id: Optional[str] = None) -> list[Client]:
    q = session.query(Client)
    if user_id is not None:
        q = q.filter(Client.user_id == user_id)
    return q.order_by(Client.created_at).all()


def get_client(session: Session, client_id: str) -> Client:
    client = session.query(Client).filter(Client.id == client_id).one_or_none()
    if client is None:
        raise NotFoundError(
            f"client '{client_id}' not found", code="client_not_found"
        )
    return client


# ---- api keys ---------------------------------------------------------------


def create_api_key(
    session: Session,
    client_id: str,
    name: str,
    expires_at: Optional[_dt.datetime] = None,
) -> tuple[ApiKey, str]:
    """Create a new API key.

    Returns (persisted_orm_row, full_token). The full token is returned
    EXACTLY ONCE and must be shown to the user immediately.
    """
    name = name.strip()
    if not name:
        raise ValidationError("name cannot be empty", code="validation_error")
    client = get_client(session, client_id)
    full_token, key_prefix, key_hash = api_keys.generate_api_key()
    api_key = ApiKey(
        client_id=client.id,
        name=name,
        key_prefix=key_prefix,
        key_hash=key_hash,
        expires_at=expires_at,
    )
    session.add(api_key)
    session.commit()
    session.refresh(api_key)
    return api_key, full_token


def list_api_keys(
    session: Session, client_id: Optional[str] = None
) -> list[ApiKey]:
    q = session.query(ApiKey)
    if client_id is not None:
        q = q.filter(ApiKey.client_id == client_id)
    return q.order_by(ApiKey.created_at).all()


def get_api_key(session: Session, api_key_id: str) -> ApiKey:
    api_key = session.query(ApiKey).filter(ApiKey.id == api_key_id).one_or_none()
    if api_key is None:
        raise NotFoundError(
            f"api key '{api_key_id}' not found", code="api_key_not_found"
        )
    return api_key


def revoke_api_key(session: Session, api_key_id: str) -> ApiKey:
    api_key = get_api_key(session, api_key_id)
    if api_key.revoked_at is None:
        api_key.revoked_at = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
        session.commit()
        session.refresh(api_key)
    return api_key


def rotate_api_key(
    session: Session,
    api_key_id: str,
    *,
    revoke_old: bool = True,
) -> tuple[ApiKey, str, Optional[ApiKey]]:
    """Rotate an API key.

    Creates a new key for the same client. If ``revoke_old`` is True (default),
    the old key is revoked immediately. Returns (new_api_key, new_full_token,
    old_api_key_or_None).
    """
    old = get_api_key(session, api_key_id)
    new_key, full_token = create_api_key(
        session=session,
        client_id=old.client_id,
        name=f"{old.name} (rotated)",
    )
    if revoke_old:
        old = revoke_api_key(session, old.id)
    return new_key, full_token, old if revoke_old else None
