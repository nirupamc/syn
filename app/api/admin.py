"""Admin / management plane API routes (M3).

All routes are protected by the admin secret (see app.core.admin_auth).

    POST   /admin/users
    GET    /admin/users
    POST   /admin/clients
    GET    /admin/clients
    POST   /admin/api-keys
    GET    /admin/api-keys
    POST   /admin/api-keys/{id}/revoke
    POST   /admin/api-keys/{id}/rotate

Inference API keys are NOT valid for these endpoints.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.admin_schemas import (
    ApiKeyCreate,
    ApiKeyCreateOut,
    ApiKeyOut,
    ApiKeyRotateOut,
    ClientCreate,
    ClientOut,
    UserCreate,
    UserOut,
)
from app.core.admin_auth import require_admin
from app.core.auth import _get_db_session
from app.logging import get_logger
from app.services import admin as admin_service

logger = get_logger("syn.api.admin")

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


# ---- users ------------------------------------------------------------------


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(request: Request, body: UserCreate) -> UserOut:
    session = _get_db_session(request)
    try:
        user = admin_service.create_user(session, body.name)
        logger.info(
            "admin: created user (user_id=%s, name=%s)", user.id, user.name
        )
        return UserOut(
            id=user.id,
            name=user.name,
            status=user.status,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
    finally:
        session.close()


@router.get("/users", response_model=list[UserOut])
async def list_users(request: Request) -> list[UserOut]:
    session = _get_db_session(request)
    try:
        users = admin_service.list_users(session)
        return [
            UserOut(
                id=u.id,
                name=u.name,
                status=u.status,
                created_at=u.created_at,
                updated_at=u.updated_at,
            )
            for u in users
        ]
    finally:
        session.close()


# ---- clients ----------------------------------------------------------------


@router.post("/clients", response_model=ClientOut, status_code=201)
async def create_client(request: Request, body: ClientCreate) -> ClientOut:
    session = _get_db_session(request)
    try:
        client = admin_service.create_client(
            session,
            user_id=body.user_id,
            name=body.name,
            description=body.description,
            allowed_models=body.allowed_models,
        )
        allowed = admin_service.get_client_allowed_models(session, client.id)
        logger.info(
            "admin: created client (client_id=%s, user_id=%s, name=%s)",
            client.id,
            client.user_id,
            client.name,
        )
        return ClientOut(
            id=client.id,
            user_id=client.user_id,
            name=client.name,
            description=client.description,
            status=client.status,
            allowed_models=allowed,
            created_at=client.created_at,
            updated_at=client.updated_at,
        )
    finally:
        session.close()


@router.get("/clients", response_model=list[ClientOut])
async def list_clients(
    request: Request, user_id: Optional[str] = None
) -> list[ClientOut]:
    session = _get_db_session(request)
    try:
        clients = admin_service.list_clients(session, user_id=user_id)
        result = []
        for c in clients:
            allowed = admin_service.get_client_allowed_models(session, c.id)
            result.append(
                ClientOut(
                    id=c.id,
                    user_id=c.user_id,
                    name=c.name,
                    description=c.description,
                    status=c.status,
                    allowed_models=allowed,
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                )
            )
        return result
    finally:
        session.close()


# ---- api keys ---------------------------------------------------------------


@router.post(
    "/api-keys",
    response_model=ApiKeyCreateOut,
    status_code=201,
)
async def create_api_key(request: Request, body: ApiKeyCreate) -> ApiKeyCreateOut:
    session = _get_db_session(request)
    try:
        api_key, full_token = admin_service.create_api_key(
            session,
            client_id=body.client_id,
            name=body.name,
            expires_at=body.expires_at,
        )
        logger.info(
            "admin: issued api key (api_key_id=%s, client_id=%s, prefix=%s)",
            api_key.id,
            api_key.client_id,
            api_key.key_prefix,
        )
        return ApiKeyCreateOut(
            id=api_key.id,
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            key=full_token,  # Returned ONCE.
            client_id=api_key.client_id,
            created_at=api_key.created_at,
            expires_at=api_key.expires_at,
        )
    finally:
        session.close()


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    request: Request, client_id: Optional[str] = None
) -> list[ApiKeyOut]:
    session = _get_db_session(request)
    try:
        keys = admin_service.list_api_keys(session, client_id=client_id)
        return [
            ApiKeyOut(
                id=k.id,
                name=k.name,
                key_prefix=k.key_prefix,
                client_id=k.client_id,
                created_at=k.created_at,
                expires_at=k.expires_at,
                revoked_at=k.revoked_at,
                last_used_at=k.last_used_at,
            )
            for k in keys
        ]
    finally:
        session.close()


@router.post("/api-keys/{api_key_id}/revoke", response_model=ApiKeyOut)
async def revoke_api_key(request: Request, api_key_id: str) -> ApiKeyOut:
    session = _get_db_session(request)
    try:
        api_key = admin_service.revoke_api_key(session, api_key_id)
        logger.info(
            "admin: revoked api key (api_key_id=%s, prefix=%s)",
            api_key.id,
            api_key.key_prefix,
        )
        return ApiKeyOut(
            id=api_key.id,
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            client_id=api_key.client_id,
            created_at=api_key.created_at,
            expires_at=api_key.expires_at,
            revoked_at=api_key.revoked_at,
            last_used_at=api_key.last_used_at,
        )
    finally:
        session.close()


@router.post(
    "/api-keys/{api_key_id}/rotate",
    response_model=ApiKeyRotateOut,
)
async def rotate_api_key(
    request: Request,
    api_key_id: str,
    revoke_old: bool = True,
) -> ApiKeyRotateOut:
    session = _get_db_session(request)
    try:
        new_key, full_token, old = admin_service.rotate_api_key(
            session, api_key_id, revoke_old=revoke_old
        )
        logger.info(
            "admin: rotated api key (new_id=%s, old_id=%s, prefix=%s)",
            new_key.id,
            old.id if old else None,
            new_key.key_prefix,
        )
        return ApiKeyRotateOut(
            id=new_key.id,
            name=new_key.name,
            key_prefix=new_key.key_prefix,
            key=full_token,  # Returned ONCE.
            client_id=new_key.client_id,
            created_at=new_key.created_at,
            expires_at=new_key.expires_at,
            rotated_from=old.id if old else None,
        )
    finally:
        session.close()


# ---- status (M4 admission visibility) --------------------------------------


@router.get("/status")
async def get_status(request: Request) -> dict[str, object]:
    """Return operational status of the admission controller.

    Exposes only safe operational information: active/queued counts and
    configured limits. No prompts, no API keys, no per-request content.
    """
    admission = getattr(request.app.state, "admission", None)
    if admission is None:
        return {
            "admission": {
                "configured": False,
                "reason": "admission controller not wired",
            }
        }
    status = await admission.status()
    return {
        "admission": {
            "active": status.active,
            "max_active": status.max_active,
            "queued": status.queued,
            "max_queue": status.max_queue,
            "queue_timeout_seconds": status.queue_timeout_seconds,
        }
    }
