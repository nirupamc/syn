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
    ClientPolicyOut,
    ClientPolicyUpdate,
    UsageSummaryOut,
    UserCreate,
    UserOut,
)
from app.core.admin_auth import require_admin
from app.core.auth import _get_db_session
from app.core.errors import NotFoundError
from app.logging import get_logger
from app.models.api_key import ApiKey
from app.models.client import Client
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


# ---- usage (M6) -------------------------------------------------------------


def _get_usage_service(request: Request):
    """Get the usage service from app state."""
    usage_service = getattr(request.app.state, "usage_service", None)
    if usage_service is None:
        from app.core.errors import SynError

        raise SynError(
            "usage service not available",
            code="usage_unavailable",
            http_status=503,
        )
    return usage_service


@router.get("/usage", response_model=UsageSummaryOut)
async def get_usage_summary(
    request: Request,
    client_id: Optional[str] = None,
    api_key_id: Optional[str] = None,
) -> UsageSummaryOut:
    """Return aggregated usage for today (UTC).

    Filters by client_id and/or api_key_id. If both are None, returns
    aggregate usage across all clients.
    """
    usage_service = _get_usage_service(request)
    session = _get_db_session(request)
    try:
        summary = usage_service.summarize(
            session,
            client_id=client_id,
            api_key_id=api_key_id,
        )
    finally:
        session.close()

    return UsageSummaryOut(
        requests=summary.requests,
        successful_requests=summary.successful_requests,
        failed_requests=summary.failed_requests,
        cancelled_requests=summary.cancelled_requests,
        rejected_requests=summary.rejected_requests,
        timed_out_requests=summary.timed_out_requests,
        requests_with_unknown_usage=summary.requests_with_unknown_usage,
        prompt_tokens=summary.prompt_tokens,
        completion_tokens=summary.completion_tokens,
        total_tokens=summary.total_tokens,
    )


@router.get("/usage/clients/{client_id}", response_model=UsageSummaryOut)
async def get_client_usage(
    request: Request, client_id: str
) -> UsageSummaryOut:
    """Return aggregated usage for a specific client (today, UTC)."""
    usage_service = _get_usage_service(request)
    session = _get_db_session(request)
    try:
        # Verify client exists
        client = session.query(Client).filter(Client.id == client_id).one_or_none()
        if client is None:
            raise NotFoundError(
                f"client '{client_id}' not found", code="client_not_found"
            )
        summary = usage_service.summarize(session, client_id=client_id)
    finally:
        session.close()

    return UsageSummaryOut(
        requests=summary.requests,
        successful_requests=summary.successful_requests,
        failed_requests=summary.failed_requests,
        cancelled_requests=summary.cancelled_requests,
        rejected_requests=summary.rejected_requests,
        timed_out_requests=summary.timed_out_requests,
        requests_with_unknown_usage=summary.requests_with_unknown_usage,
        prompt_tokens=summary.prompt_tokens,
        completion_tokens=summary.completion_tokens,
        total_tokens=summary.total_tokens,
    )


@router.get("/usage/keys/{api_key_id}", response_model=UsageSummaryOut)
async def get_api_key_usage(
    request: Request, api_key_id: str
) -> UsageSummaryOut:
    """Return aggregated usage for a specific API key (today, UTC)."""
    usage_service = _get_usage_service(request)
    session = _get_db_session(request)
    try:
        # Verify key exists
        api_key = (
            session.query(ApiKey).filter(ApiKey.id == api_key_id).one_or_none()
        )
        if api_key is None:
            raise NotFoundError(
                f"api key '{api_key_id}' not found", code="api_key_not_found"
            )
        summary = usage_service.summarize(session, api_key_id=api_key_id)
    finally:
        session.close()

    return UsageSummaryOut(
        requests=summary.requests,
        successful_requests=summary.successful_requests,
        failed_requests=summary.failed_requests,
        cancelled_requests=summary.cancelled_requests,
        rejected_requests=summary.rejected_requests,
        timed_out_requests=summary.timed_out_requests,
        requests_with_unknown_usage=summary.requests_with_unknown_usage,
        prompt_tokens=summary.prompt_tokens,
        completion_tokens=summary.completion_tokens,
        total_tokens=summary.total_tokens,
    )


@router.get("/clients/{client_id}/policy", response_model=ClientPolicyOut)
async def get_client_policy(
    request: Request, client_id: str
) -> ClientPolicyOut:
    """Return a client's policy (limits)."""
    session = _get_db_session(request)
    try:
        client = session.query(Client).filter(Client.id == client_id).one_or_none()
        if client is None:
            raise NotFoundError(
                f"client '{client_id}' not found", code="client_not_found"
            )
        return ClientPolicyOut(
            id=client.id,
            name=client.name,
            requests_per_minute=client.requests_per_minute,
            requests_per_day=client.requests_per_day,
            tokens_per_day=client.tokens_per_day,
        )
    finally:
        session.close()


@router.put("/clients/{client_id}/policy", response_model=ClientPolicyOut)
async def update_client_policy(
    request: Request, client_id: str, body: ClientPolicyUpdate
) -> ClientPolicyOut:
    """Update a client's policy (limits). Pass null to clear a field."""
    session = _get_db_session(request)
    try:
        client = session.query(Client).filter(Client.id == client_id).one_or_none()
        if client is None:
            raise NotFoundError(
                f"client '{client_id}' not found", code="client_not_found"
            )
        # Update only the fields that were explicitly provided.
        if "requests_per_minute" in body.model_fields_set:
            client.requests_per_minute = body.requests_per_minute
        if "requests_per_day" in body.model_fields_set:
            client.requests_per_day = body.requests_per_day
        if "tokens_per_day" in body.model_fields_set:
            client.tokens_per_day = body.tokens_per_day
        session.commit()
        session.refresh(client)
        logger.info(
            "admin: updated client policy (client_id=%s, rpm=%s, rpd=%s, tpd=%s)",
            client.id,
            client.requests_per_minute,
            client.requests_per_day,
            client.tokens_per_day,
        )
        return ClientPolicyOut(
            id=client.id,
            name=client.name,
            requests_per_minute=client.requests_per_minute,
            requests_per_day=client.requests_per_day,
            tokens_per_day=client.tokens_per_day,
        )
    finally:
        session.close()
