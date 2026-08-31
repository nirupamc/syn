"""Admin / management plane API routes (M3 + M7 observability + M9 routing).

All routes are protected by the admin secret (see app.core.admin_auth).

M3 endpoints:
    POST   /admin/users
    GET    /admin/users
    POST   /admin/clients
    GET    /admin/clients
    POST   /admin/api-keys
    GET    /admin/api-keys
    POST   /admin/api-keys/{id}/revoke
    POST   /admin/api-keys/{id}/rotate
    GET    /admin/status
    GET    /admin/usage
    GET    /admin/usage/clients/{client_id}
    GET    /admin/usage/keys/{api_key_id}
    GET    /admin/clients/{client_id}/policy
    PUT    /admin/clients/{client_id}/policy

M7 observability endpoints:
    GET    /admin/observability/summary
    GET    /admin/observability/latency
    GET    /admin/observability/recent
    GET    /admin/observability/clients
    GET    /admin/observability/models
    GET    /admin/dashboard

M9 routing endpoints:
    POST   /admin/routing/preview
    GET    /admin/observability/backends

M10 admin UI introspection endpoints:
    GET    /admin/overview
    GET    /admin/models
    GET    /admin/backends
    GET    /admin/settings
    GET    /admin/ui

Inference API keys are NOT valid for these endpoints.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.admin_schemas import (
    ApiKeyCreate,
    ApiKeyCreateOut,
    ApiKeyOut,
    ApiKeyRotateOut,
    BackendsListOut,
    BackendListItem,
    BackendBreakdownOut,
    BackendHealthOut,
    ClientBreakdownOut,
    ClientCreate,
    ClientOut,
    ClientPolicyOut,
    ClientPolicyUpdate,
    LatencyDetailOut,
    LatencyStatsOut,
    ModelBreakdownOut,
    ModelListOut,
    ModelsListOut,
    ObservabilitySummaryOut,
    OverviewOut,
    RecentRequestOut,
    RequestOutcomeOut,
    RoutingPreviewRequest,
    RoutingPreviewResponse,
    SettingsOut,
    TokenSummaryOut,
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

# Public routes (no auth dependency) — served separately from the admin
# management plane so the UI shell can render before the operator enters
# the admin secret. Auth for data endpoints remains enforced above.
ui_router = APIRouter(prefix="/admin", tags=["admin"])


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
    """Return operational status of the admission controller and routing (M9).

    Exposes only safe operational information: active/queued counts,
    configured limits, routing mode, and per-backend health. No prompts,
    no API keys, no per-request content.
    """
    result: dict[str, object] = {}

    # Admission controller (M4)
    admission = getattr(request.app.state, "admission", None)
    if admission is None:
        result["admission"] = {
            "configured": False,
            "reason": "admission controller not wired",
        }
    else:
        status = await admission.status()
        result["admission"] = {
            "active": status.active,
            "max_active": status.max_active,
            "queued": status.queued,
            "max_queue": status.max_queue,
            "queue_timeout_seconds": status.queue_timeout_seconds,
        }

    # Routing (M9)
    routing_svc = getattr(request.app.state, "router", None)
    if routing_svc is None:
        result["routing"] = {
            "configured": False,
            "mode": "passthrough",
            "reason": "routing service not wired",
        }
    elif not routing_svc.configured:
        result["routing"] = {
            "configured": False,
            "mode": "passthrough",
        }
    else:
        backend_registry = routing_svc.backend_registry
        backends_info: dict[str, object] = {}
        if backend_registry is not None:
            for bid in backend_registry.ids():
                try:
                    health = await backend_registry.health(bid)
                    backends_info[bid] = {
                        "type": backend_registry.types().get(bid, "unknown"),
                        "health": {
                            "reachable": health.reachable,
                            "state": health.state.value
                            if hasattr(health.state, "value")
                            else str(health.state),
                            "reason": health.reason,
                        },
                    }
                except Exception:  # noqa: BLE001
                    backends_info[bid] = {
                        "type": backend_registry.types().get(bid, "unknown"),
                        "health": {
                            "reachable": False,
                            "state": "error",
                            "reason": "health probe failed",
                        },
                    }
        result["routing"] = {
            "configured": True,
            "mode": "configured",
            "backends": backends_info,
        }

    return result


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


# ---- M7 observability endpoints --------------------------------------------


def _get_observability_service(request: Request):
    """Get the observability service from app state."""
    svc = getattr(request.app.state, "observability_service", None)
    if svc is None:
        from app.core.errors import SynError

        raise SynError(
            "observability service not available",
            code="observability_unavailable",
            http_status=503,
        )
    return svc


def _get_admission_for_obs(request: Request):
    """Get admission controller for status exposure."""
    return getattr(request.app.state, "admission", None)


@router.get("/observability/summary", response_model=ObservabilitySummaryOut)
async def observability_summary(request: Request) -> ObservabilitySummaryOut:
    """Full observability summary: outcomes, tokens, latency, admission state."""
    obs = _get_observability_service(request)
    admission = _get_admission_for_obs(request)

    admission_status = await admission.status() if admission else None
    active = admission_status.active if admission_status else 0
    queued = admission_status.queued if admission_status else 0

    session = _get_db_session(request)
    try:
        summary = obs.summary(
            session,
            admission_active=active,
            admission_queued=queued,
        )
    finally:
        session.close()

    return ObservabilitySummaryOut(
        requests=RequestOutcomeOut(
            completed=summary.requests.completed,
            failed=summary.requests.failed,
            cancelled=summary.requests.cancelled,
            timed_out=summary.requests.timed_out,
            rejected=summary.requests.rejected,
        ),
        tokens=TokenSummaryOut(
            prompt_tokens=summary.tokens.prompt_tokens,
            completion_tokens=summary.tokens.completion_tokens,
            total_tokens=summary.tokens.total_tokens,
            requests_with_unknown_usage=summary.tokens.requests_with_unknown_usage,
        ),
        latency=LatencyStatsOut(
            count=summary.latency.count,
            avg_ms=summary.latency.avg_ms,
            p50_ms=summary.latency.p50_ms,
            p95_ms=summary.latency.p95_ms,
            max_ms=summary.latency.max_ms,
        ),
        active=summary.active,
        queued=summary.queued,
    )


@router.get("/observability/latency", response_model=LatencyDetailOut)
async def observability_latency(request: Request) -> LatencyDetailOut:
    """Latency statistics for all measured dimensions."""
    obs = _get_observability_service(request)
    session = _get_db_session(request)
    try:
        stats = obs.latency_stats(session)
    finally:
        session.close()

    def _to_out(s) -> LatencyStatsOut:
        return LatencyStatsOut(
            count=s.count, avg_ms=s.avg_ms, p50_ms=s.p50_ms, p95_ms=s.p95_ms, max_ms=s.max_ms
        )

    return LatencyDetailOut(
        total_duration_ms=_to_out(stats["total_duration_ms"]),
        queue_wait_ms=_to_out(stats["queue_wait_ms"]),
        backend_latency_ms=_to_out(stats["backend_latency_ms"]),
        ttft_ms=_to_out(stats["ttft_ms"]),
        stream_duration_ms=_to_out(stats["stream_duration_ms"]),
    )


@router.get("/observability/recent", response_model=list[RecentRequestOut])
async def observability_recent(
    request: Request, limit: int = 50
) -> list[RecentRequestOut]:
    """Most recent requests (newest first). Max 200."""
    obs = _get_observability_service(request)
    session = _get_db_session(request)
    try:
        recent = obs.recent_requests(session, limit=limit)
    finally:
        session.close()

    return [
        RecentRequestOut(
            request_id=r.request_id,
            client_id=r.client_id,
            model=r.model,
            streaming=r.streaming,
            status=r.status,
            error_code=r.error_code,
            started_at=r.started_at,
            completed_at=r.completed_at,
            total_duration_ms=r.total_duration_ms,
            ttft_ms=r.ttft_ms,
            total_tokens=r.total_tokens,
            queue_wait_ms=r.queue_wait_ms,
        )
        for r in recent
    ]


@router.get("/observability/clients", response_model=list[ClientBreakdownOut])
async def observability_clients(request: Request) -> list[ClientBreakdownOut]:
    """Request breakdown by client."""
    obs = _get_observability_service(request)
    session = _get_db_session(request)
    try:
        breakdown = obs.client_breakdown(session)
    finally:
        session.close()

    return [
        ClientBreakdownOut(
            client_id=c.client_id,
            requests=c.requests,
            completed=c.completed,
            failed=c.failed,
            cancelled=c.cancelled,
            total_tokens=c.total_tokens,
        )
        for c in breakdown
    ]


@router.get("/observability/models", response_model=list[ModelBreakdownOut])
async def observability_models(request: Request) -> list[ModelBreakdownOut]:
    """Request breakdown by model."""
    obs = _get_observability_service(request)
    session = _get_db_session(request)
    try:
        breakdown = obs.model_breakdown(session)
    finally:
        session.close()

    return [
        ModelBreakdownOut(
            model=m.model,
            requests=m.requests,
            completed=m.completed,
            failed=m.failed,
            cancelled=m.cancelled,
            total_tokens=m.total_tokens,
        )
        for m in breakdown
    ]


# ---- M9 routing endpoints --------------------------------------------------


@router.post("/routing/preview", response_model=RoutingPreviewResponse)
async def routing_preview(
    request: Request, body: RoutingPreviewRequest
) -> RoutingPreviewResponse:
    """Preview the routing decision for a requested model.

    Returns safe operational information: canonical model, backend id,
    resolution reason, and backend reachability. Does NOT expose backend
    filesystem paths, credentials, or secret URLs.
    """
    routing_svc = getattr(request.app.state, "router", None)
    if routing_svc is None:
        from app.core.errors import SynError

        raise SynError(
            "routing service not available",
            code="routing_not_wired",
            http_status=503,
        )

    # Build a dummy principal for preview (does not enforce access).
    from app.core.principal import AuthenticatedPrincipal

    preview_principal = AuthenticatedPrincipal(
        user_id="preview",
        user_name="preview",
        client_id="preview",
        client_name="preview",
        api_key_id="preview",
        api_key_prefix="preview",
        allowed_models=None,
    )

    try:
        decision = await routing_svc.route(body.model, preview_principal)
    except Exception as exc:
        from app.core.errors import SynError

        raise SynError(
            f"routing preview failed: {exc}",
            code="routing_preview_failed",
            http_status=400,
        ) from exc

    # Check backend reachability (cached, non-blocking).
    backend_reachable = True
    if routing_svc.backend_registry is not None:
        try:
            health = await routing_svc.backend_registry.health(decision.backend_id)
            backend_reachable = health.reachable
        except Exception:  # noqa: BLE001
            backend_reachable = False

    return RoutingPreviewResponse(
        requested_model=decision.requested_model,
        canonical_model=decision.canonical_model,
        backend_id=decision.backend_id,
        reason=decision.reason,
        backend_reachable=backend_reachable,
    )


@router.get("/observability/backends", response_model=list[BackendBreakdownOut])
async def observability_backends(request: Request) -> list[BackendBreakdownOut]:
    """Request breakdown by backend (M9)."""
    obs = _get_observability_service(request)
    session = _get_db_session(request)
    try:
        breakdown = obs.backend_breakdown(session)
    finally:
        session.close()

    return [
        BackendBreakdownOut(
            backend_id=b.backend_id,
            requests=b.requests,
            completed=b.completed,
            failed=b.failed,
            cancelled=b.cancelled,
            total_tokens=b.total_tokens,
        )
        for b in breakdown
    ]


@router.get("/dashboard")
async def admin_dashboard(request: Request):
    """Admin dashboard (server-rendered HTML).

    Protected by admin auth (via router-level dependency).
    Displays real operational data from the observability service.
    """
    obs = _get_observability_service(request)
    admission = _get_admission_for_obs(request)

    # Gather data
    admission_status = await admission.status() if admission else None
    active = admission_status.active if admission_status else 0
    queued = admission_status.queued if admission_status else 0
    max_active = admission_status.max_active if admission_status else 0
    max_queue = admission_status.max_queue if admission_status else 0

    session = _get_db_session(request)
    try:
        summary = obs.summary(session, admission_active=active, admission_queued=queued)
        latency_stats = obs.latency_stats(session)
        recent = obs.recent_requests(session, limit=20)
    finally:
        session.close()

    # Backend health (async probe)
    backend = getattr(request.app.state, "backend", None)
    backend_state = "unknown"
    backend_reachable = False
    if backend is not None:
        try:
            health_result = await backend.health()
            backend_state = health_result.state.value if hasattr(health_result.state, "value") else str(health_result.state)
            backend_reachable = health_result.reachable
        except Exception:
            backend_state = "unreachable"
            backend_reachable = False

    # Build HTML dashboard
    html = _render_dashboard(
        summary=summary,
        latency_stats=latency_stats,
        recent=recent,
        backend_state=backend_state,
        backend_reachable=backend_reachable,
        active=active,
        queued=queued,
        max_active=max_active,
        max_queue=max_queue,
    )

    return HTMLResponse(content=html)


@router.get("/metrics")
async def prometheus_metrics(request: Request):
    """Prometheus-compatible text metrics endpoint.

    Admin-protected. Low cardinality labels only. No secrets.
    """
    from fastapi.responses import PlainTextResponse

    obs = _get_observability_service(request)
    admission = _get_admission_for_obs(request)

    admission_status = await admission.status() if admission else None
    active = admission_status.active if admission_status else 0
    queued = admission_status.queued if admission_status else 0

    session = _get_db_session(request)
    try:
        summary = obs.summary(session, admission_active=active, admission_queued=queued)
    finally:
        session.close()

    def _f(v):
        return f"{v}" if v is not None else "0"

    lines = [
        "# HELP syn_requests_total Total inference requests",
        "# TYPE syn_requests_total counter",
        f'syn_requests_total{{status="completed"}} {summary.requests.completed}',
        f'syn_requests_total{{status="failed"}} {summary.requests.failed}',
        f'syn_requests_total{{status="cancelled"}} {summary.requests.cancelled}',
        f'syn_requests_total{{status="timed_out"}} {summary.requests.timed_out}',
        f'syn_requests_total{{status="rejected"}} {summary.requests.rejected}',
        "",
        "# HELP syn_active_requests Currently active requests",
        "# TYPE syn_active_requests gauge",
        f"syn_active_requests {active}",
        "",
        "# HELP syn_queued_requests Currently queued requests",
        "# TYPE syn_queued_requests gauge",
        f"syn_queued_requests {queued}",
        "",
        "# HELP syn_tokens_total Total tokens processed",
        "# TYPE syn_tokens_total counter",
        f'syn_tokens_total{{type="prompt"}} {summary.tokens.prompt_tokens}',
        f'syn_tokens_total{{type="completion"}} {summary.tokens.completion_tokens}',
        f'syn_tokens_total{{type="total"}} {summary.tokens.total_tokens}',
        "",
        "# HELP syn_request_duration_seconds Request duration statistics",
        "# TYPE syn_request_duration_seconds summary",
        f'syn_request_duration_seconds{{quantile="0.5"}} {_f(summary.latency.p50_ms and summary.latency.p50_ms / 1000)}',
        f'syn_request_duration_seconds{{quantile="0.95"}} {_f(summary.latency.p95_ms and summary.latency.p95_ms / 1000)}',
        f'syn_request_duration_seconds{{quantile="1.0"}} {_f(summary.latency.max_ms and summary.latency.max_ms / 1000)}',
        "",
        "# HELP syn_ttft_seconds Time to first token statistics",
        "# TYPE syn_ttft_seconds summary",
    ]

    obs2 = _get_observability_service(request)
    session2 = _get_db_session(request)
    try:
        ttft_stats_map = obs2.latency_stats(session2)
        ttft_stats = ttft_stats_map.get("ttft_ms")
    finally:
        session2.close()

    if ttft_stats and ttft_stats.count > 0:
        lines.extend([
            f'syn_ttft_seconds{{quantile="0.5"}} {_f(ttft_stats.p50_ms and ttft_stats.p50_ms / 1000)}',
            f'syn_ttft_seconds{{quantile="0.95"}} {_f(ttft_stats.p95_ms and ttft_stats.p95_ms / 1000)}',
        ])
    else:
        lines.extend([
            'syn_ttft_seconds{{quantile="0.5"}} 0',
            'syn_ttft_seconds{{quantile="0.95"}} 0',
        ])

    return PlainTextResponse(content="\n".join(lines) + "\n")


@ui_router.get("/ui")
async def admin_ui(request: Request):
    """Admin control plane UI.

    Serves the safe HTML shell without admin auth. Authentication is performed
    client-side: the operator enters the admin secret in the browser, which is
    held only in JS memory and sent as the X-Admin-Secret header on subsequent
    admin API requests. This ensures the secret is never embedded in the served
    HTML or persisted via localStorage/sessionStorage.
    """
    return HTMLResponse(content=render_admin_ui())


# ---- M10 admin UI introspection endpoints ----------------------------------


@router.get("/overview", response_model=OverviewOut)
async def get_overview(request: Request) -> OverviewOut:
    """Aggregated operational overview for the admin UI.

    Returns safe, operator-facing data only: service status, routing mode,
    admission state, aggregate request/token counts, and latency/TTFT stats.

    Does NOT expose prompts, responses, API keys, admin secrets, or
    backend-native model paths.
    """
    obs = _get_observability_service(request)
    admission = _get_admission_for_obs(request)
    routing_svc = getattr(request.app.state, "router", None)

    # Service status
    obs_service_available = obs is not None
    syn_healthy = obs_service_available

    # Admission state
    admission_status = await admission.status() if admission else None
    active = admission_status.active if admission_status else 0
    queued = admission_status.queued if admission_status else 0

    # Routing mode
    routing_configured = routing_svc is not None and routing_svc.configured

    # Backend list (for overview cards)
    backend_items: list[BackendListItem] = []
    if routing_svc is not None and routing_svc.backend_registry is not None:
        backend_registry = routing_svc.backend_registry
        for bid in backend_registry.ids():
            try:
                health = await backend_registry.health(bid)
                backend_items.append(
                    BackendListItem(
                        id=bid,
                        type=backend_registry.types().get(bid, "unknown"),
                        reachable=health.reachable,
                        state=health.state.value
                        if hasattr(health.state, "value")
                        else str(health.state),
                        reason=health.reason or "",
                    )
                )
            except Exception:
                backend_items.append(
                    BackendListItem(
                        id=bid,
                        type=backend_registry.types().get(bid, "unknown"),
                        reachable=False,
                        state="error",
                        reason="health probe failed",
                    )
                )
    elif routing_svc is not None and not routing_configured:
        backend_items.append(
            BackendListItem(
                id="default",
                type="passthrough",
                reachable=False,
                state="unknown",
                reason="passthrough mode",
            )
        )

    # Observability aggregates
    session = _get_db_session(request)
    try:
        summary = obs.summary(
            session, admission_active=active, admission_queued=queued
        )
        latency_map = obs.latency_stats(session)
    finally:
        session.close()

    # Build admission dict for OverviewOut
    admission_dict: dict[str, object] = {
        "active": active,
        "max_active": admission_status.max_active if admission_status else 0,
        "queued": queued,
        "max_queue": admission_status.max_queue if admission_status else 0,
        "queue_timeout_seconds": admission_status.queue_timeout_seconds
        if admission_status
        else 0.0,
    }

    latency_stats = latency_map.get("total_duration_ms")
    ttft_stats = latency_map.get("ttft_ms")

    return OverviewOut(
        syn_healthy=syn_healthy,
        routing_configured=routing_configured,
        routing_mode="configured" if routing_configured else "passthrough",
        admission=admission_dict,
        backends=backend_items,
        requests={
            "completed": summary.requests.completed,
            "failed": summary.requests.failed,
            "cancelled": summary.requests.cancelled,
            "timed_out": summary.requests.timed_out,
            "rejected": summary.requests.rejected,
        },
        tokens={
            "prompt": summary.tokens.prompt_tokens,
            "completion": summary.tokens.completion_tokens,
            "total": summary.tokens.total_tokens,
        },
        latency_ms={
            "avg_ms": latency_stats.avg_ms if latency_stats else None,
            "p50_ms": latency_stats.p50_ms if latency_stats else None,
            "p95_ms": latency_stats.p95_ms if latency_stats else None,
        },
        ttft_ms={
            "avg_ms": ttft_stats.avg_ms if ttft_stats else None,
            "p50_ms": ttft_stats.p50_ms if ttft_stats else None,
            "p95_ms": ttft_stats.p95_ms if ttft_stats else None,
        },
    )


@router.get("/models", response_model=ModelsListOut)
async def get_models(request: Request) -> ModelsListOut:
    """List canonical Syn models (public IDs only).

    In configured (multi-backend) mode, returns models from the routing
    config registry. In passthrough mode, returns an empty list — models are
    discovered dynamically from the backend on each /v1/models request and are
    not part of the static admin registry.

    Does NOT expose backend-native model identifiers (e.g. GGUF filesystem paths).
    """
    routing_svc = getattr(request.app.state, "router", None)

    if routing_svc is None or not routing_svc.configured:
        return ModelsListOut(configured=False, models=[])

    model_registry = routing_svc.model_registry
    if model_registry is None:
        return ModelsListOut(configured=False, models=[])

    models = [
        ModelListOut(
            id=entry.id,
            backend_id=entry.backend_id,
            enabled=entry.enabled,
            aliases=list(entry.aliases),
        )
        for entry in model_registry.list_all()
    ]

    return ModelsListOut(configured=True, models=models)


@router.get("/backends", response_model=BackendsListOut)
async def get_backends(request: Request) -> BackendsListOut:
    """List configured backends with their current health.

    Does NOT expose backend credentials or internal URLs.
    """
    routing_svc = getattr(request.app.state, "router", None)

    if routing_svc is None or not routing_svc.configured:
        return BackendsListOut(configured=False, backends=[])

    backend_registry = routing_svc.backend_registry
    if backend_registry is None:
        return BackendsListOut(configured=False, backends=[])

    backends: list[BackendListItem] = []
    for bid in backend_registry.ids():
        try:
            health = await backend_registry.health(bid)
            backends.append(
                BackendListItem(
                    id=bid,
                    type=backend_registry.types().get(bid, "unknown"),
                    reachable=health.reachable,
                    state=health.state.value
                    if hasattr(health.state, "value")
                    else str(health.state),
                    reason=health.reason or "",
                )
            )
        except Exception:
            backends.append(
                BackendListItem(
                    id=bid,
                    type=backend_registry.types().get(bid, "unknown"),
                    reachable=False,
                    state="error",
                    reason="health probe failed",
                )
            )

    return BackendsListOut(configured=True, backends=backends)


@router.get("/settings", response_model=SettingsOut)
async def get_settings(request: Request) -> SettingsOut:
    """Return safe, operator-facing runtime configuration.

    NEVER exposes: admin_secret, API keys, tokens, .env contents, or
    full private routing-config filesystem paths.
    """
    settings = getattr(request.app.state, "settings", None)
    routing_svc = getattr(request.app.state, "router", None)

    from app.config import Settings  # noqa: PLC0415

    if isinstance(settings, Settings):
        cors_origins = settings.cors_origins_list
        queue_timeout = settings.queue_timeout_seconds
        max_active = settings.max_active_requests
        max_queued = settings.max_queue_size
        max_body = settings.max_request_body_bytes
        admin_auth_configured = bool(settings.admin_secret)
        routing_file_path = settings.routing_config_path
    else:
        cors_origins = []
        queue_timeout = None
        max_active = None
        max_queued = None
        max_body = None
        admin_auth_configured = False
        routing_file_path = None

    routing_configured = routing_svc is not None and routing_svc.configured

    # Avoid exposing full local filesystem paths; return basename only
    safe_routing_path = None
    if routing_file_path:
        import os  # noqa: PLC0415

        safe_routing_path = os.path.basename(routing_file_path)

    return SettingsOut(
        request_size_limit_bytes=max_body,
        cors_allowed_origins=list(cors_origins),
        queue_timeout_seconds=queue_timeout,
        max_active_requests=max_active,
        max_queued_requests=max_queued,
        admin_auth_configured=admin_auth_configured,
        routing_file_path=safe_routing_path,
    )


def _render_dashboard(
    *,
    summary,
    latency_stats,
    recent,
    backend_state,
    backend_reachable,
    active,
    queued,
    max_active,
    max_queue,
) -> str:
    """Render the admin dashboard as server-side HTML."""

    def _ms(v):
        return f"{v}ms" if v is not None else "-"

    def _cls(status: str) -> str:
        return {
            "completed": "ok",
            "failed": "err",
            "cancelled": "warn",
            "timed_out": "warn",
            "rejected": "err",
        }.get(status, "")

    rows = ""
    for r in recent:
        started = r.started_at.strftime("%H:%M:%S") if r.started_at else "-"
        dur = _ms(r.total_duration_ms)
        tok = str(r.total_tokens) if r.total_tokens is not None else "-"
        rows += f"""<tr>
            <td>{r.request_id[:12]}...</td>
            <td>{started}</td>
            <td>{r.model}</td>
            <td>{"S" if r.streaming else "NS"}</td>
            <td class="{_cls(r.status)}">{r.status}</td>
            <td>{r.error_code or '-'}</td>
            <td>{dur}</td>
            <td>{_ms(r.ttft_ms)}</td>
            <td>{tok}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Syn Admin Dashboard</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }}
h1 {{ color: #58a6ff; margin-bottom: 16px; font-size: 1.4em; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 14px; }}
.card .label {{ font-size: 0.75em; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; display: block; }}
.card .value {{ font-size: 1.5em; font-weight: 600; margin-top: 4px; }}
.card .value.ok {{ color: #3fb950; }}
.card .value.err {{ color: #f85149; }}
.card .value.warn {{ color: #d29922; }}
h2 {{ color: #58a6ff; font-size: 1.1em; margin: 20px 0 10px; }}
table {{ width: 100%; border-collapse: collapse; background: #161b22; border: 1px solid #30363d; border-radius: 6px; overflow: hidden; font-size: 0.85em; }}
th {{ background: #21262d; color: #8b949e; text-align: left; padding: 8px 10px; font-weight: 500; }}
td {{ padding: 6px 10px; border-top: 1px solid #21262d; }}
.ok {{ color: #3fb950; }}
.err {{ color: #f85149; }}
.warn {{ color: #d29922; }}
.meta {{ color: #8b949e; font-size: 0.8em; margin-top: 20px; }}
</style>
</head>
<body>
<h1>Syn Admin Dashboard</h1>
<div class="cards">
  <div class="card">
    <div class="label">Service</div>
    <div class="value ok">Running</div>
  </div>
  <div class="card">
    <div class="label">Backend</div>
    <div class="value {'ok' if backend_reachable else 'err'}">{backend_state}</div>
  </div>
  <div class="card">
    <div class="label">Active</div>
    <div class="value blue">{active}/{max_active}</div>
  </div>
  <div class="card">
    <div class="label">Queued</div>
    <div class="value blue">{queued}/{max_queue}</div>
  </div>
</div>
<div class="cards">
  <div class="card">
    <div class="label">Completed</div>
    <div class="value ok">{summary.requests.completed}</div>
  </div>
  <div class="card">
    <div class="label">Failed</div>
    <div class="value err">{summary.requests.failed}</div>
  </div>
  <div class="card">
    <div class="label">Cancelled</div>
    <div class="value warn">{summary.requests.cancelled}</div>
  </div>
  <div class="card">
    <div class="label">Rejected</div>
    <div class="value err">{summary.requests.rejected + summary.requests.timed_out}</div>
  </div>
</div>
<div class="cards">
  <div class="card">
    <div class="label">Prompt Tokens</div>
    <div class="value blue">{summary.tokens.prompt_tokens:,}</div>
  </div>
  <div class="card">
    <div class="label">Completion Tokens</div>
    <div class="value blue">{summary.tokens.completion_tokens:,}</div>
  </div>
  <div class="card">
    <div class="label">Total Tokens</div>
    <div class="value blue">{summary.tokens.total_tokens:,}</div>
  </div>
</div>
<div class="cards">
  <div class="label">Avg Latency</div>
    <div class="value blue">{_ms(summary.latency.avg_ms)}</div>
  <div class="card">
    <div class="label">P50 Latency</div>
    <div class="value blue">{_ms(summary.latency.p50_ms)}</div>
  <div class="card">
    <div class="label">P95 Latency</div>
    <div class="value blue">{_ms(summary.latency.p95_ms)}</div>
  <div class="card">
    <div class="label">Avg TTFT</div>
    <div class="value blue">{_ms(latency_stats['ttft_ms'].avg_ms)}</div>
  </div>

<h2>Recent Requests</h2>
<table>
<thead>
<tr><th>Request ID</th><th>Time</th><th>Model</th><th>Stream</th><th>Status</th><th>Error</th><th>Duration</th><th>TTFT</th><th>Tokens</th></tr>
</thead>
<tbody>{rows if rows else '<tr><td colspan="9" style="text-align:center;color:#8b949e;">No requests yet</td></tr>'}</tbody>
</table>

<div class="meta">Auto-refreshes every 5 seconds. All times UTC.</div>
</body>
</html>"""

def render_admin_ui() -> str:
    """Render the admin UI HTML.

    Returns a server-rendered operational console for Syn administration.
    Auth is via X-Admin-Secret sent in memory (not persisted in HTML or JS).
    """
    import os
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    template_file = os.path.join(base, "app", "templates", "admin_base.html")
    if os.path.exists(template_file):
        with open(template_file) as f:
            return f.read()
    # Fallback minimal UI
    return """<!doctype html><html><head><meta charset="utf-8"><title>Syn Admin</title></head><body><h1>Syn Admin UI</h1><p>Place admin_base.html in app/templates/.</p></body></html>"""
