"""Admin / management plane API schemas (M3).

Typed request/response models for the /admin/* endpoints. These are NOT
exposed to the data plane; they are management operations protected by a
separate bootstrap secret.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---- users ------------------------------------------------------------------


class UserCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class UserOut(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


# ---- clients ----------------------------------------------------------------


class ClientCreate(BaseModel):
    user_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    allowed_models: Optional[list[str]] = None


class ClientOut(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str]
    status: str
    allowed_models: list[str]
    created_at: datetime
    updated_at: datetime


# ---- api keys ---------------------------------------------------------------


class ApiKeyCreate(BaseModel):
    client_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=255)
    expires_at: Optional[datetime] = None


class ApiKeyCreateOut(BaseModel):
    """Response when a key is created/rotated: includes the full secret ONCE."""

    id: str
    name: str
    key_prefix: str
    key: str  # Full token. Returned ONLY at creation/rotation.
    client_id: str
    created_at: datetime
    expires_at: Optional[datetime]


class ApiKeyOut(BaseModel):
    """Listing metadata: NEVER includes the secret or hash."""

    id: str
    name: str
    key_prefix: str
    client_id: str
    created_at: datetime
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    last_used_at: Optional[datetime]


class ApiKeyRotateOut(ApiKeyCreateOut):
    """Rotation response: new key returned once, plus old key id if revoked."""

    rotated_from: Optional[str] = None


# ---- usage (M6) ------------------------------------------------------------


class UsageSummaryOut(BaseModel):
    """Aggregated usage summary for a key/client/period."""

    requests: int
    successful_requests: int
    failed_requests: int
    cancelled_requests: int
    rejected_requests: int
    timed_out_requests: int
    requests_with_unknown_usage: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ClientPolicyOut(BaseModel):
    """Client-level policy (limits)."""

    id: str
    name: str
    requests_per_minute: Optional[int] = None
    requests_per_day: Optional[int] = None
    tokens_per_day: Optional[int] = None


class ClientPolicyUpdate(BaseModel):
    """Update request for a client's policy."""

    requests_per_minute: Optional[int] = Field(default=None, ge=0)
    requests_per_day: Optional[int] = Field(default=None, ge=0)
    tokens_per_day: Optional[int] = Field(default=None, ge=0)


# ---- M7 observability schemas ----------------------------------------------


class LatencyStatsOut(BaseModel):
    """Latency statistics for a single metric."""

    count: int
    avg_ms: Optional[int] = None
    p50_ms: Optional[int] = None
    p95_ms: Optional[int] = None
    max_ms: Optional[int] = None


class RequestOutcomeOut(BaseModel):
    """Request counts by outcome."""

    completed: int
    failed: int
    cancelled: int
    timed_out: int
    rejected: int


class TokenSummaryOut(BaseModel):
    """Aggregate token counts."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    requests_with_unknown_usage: int


class ObservabilitySummaryOut(BaseModel):
    """Full admin observability summary."""

    requests: RequestOutcomeOut
    tokens: TokenSummaryOut
    latency: LatencyStatsOut
    active: int
    queued: int


class LatencyDetailOut(BaseModel):
    """Latency stats for all measured dimensions."""

    total_duration_ms: LatencyStatsOut
    queue_wait_ms: LatencyStatsOut
    backend_latency_ms: LatencyStatsOut
    ttft_ms: LatencyStatsOut
    stream_duration_ms: LatencyStatsOut


class RecentRequestOut(BaseModel):
    """A single recent request for the admin dashboard."""

    request_id: str
    client_id: Optional[str] = None
    model: str
    streaming: bool
    status: str
    error_code: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_duration_ms: Optional[int] = None
    ttft_ms: Optional[int] = None
    total_tokens: Optional[int] = None
    queue_wait_ms: Optional[int] = None


class ClientBreakdownOut(BaseModel):
    """Request counts by client."""

    client_id: Optional[str] = None
    requests: int
    completed: int
    failed: int
    cancelled: int
    total_tokens: int


class ModelBreakdownOut(BaseModel):
    """Request counts by model."""

    model: str
    requests: int
    completed: int
    failed: int
    cancelled: int
    total_tokens: int


class BackendHealthOut(BaseModel):
    """Backend health for dashboard."""

    configured: bool
    reachable: bool
    state: str
    reason: str = ""


# ---- M9 routing schemas ---------------------------------------------------


class RoutingPreviewRequest(BaseModel):
    """Request body for /admin/routing/preview."""

    model: str = Field(..., min_length=1, max_length=255)


class RoutingPreviewResponse(BaseModel):
    """Safe preview of a routing decision (no native paths or credentials)."""

    requested_model: str
    canonical_model: str
    backend_id: str
    reason: str
    backend_reachable: bool


class BackendBreakdownOut(BaseModel):
    """Request counts by backend (M9)."""

    backend_id: Optional[str] = None
    requests: int
    completed: int
    failed: int
    cancelled: int
    total_tokens: int


# ---- M10 admin UI schemas -------------------------------------------------


class ModelListOut(BaseModel):
    """A single canonical Syn model entry (M10 UI).

    Deliberately omits the backend-native filesystem path. UI consumers must
    never render backend-native GGUF paths.

    Runtime fields are derived from real backend health and model-discovery
    probes. ``enabled`` is a configuration flag and does NOT imply
    ``runtime_loaded``.
    """

    id: str
    backend_id: str
    enabled: bool
    aliases: list[str] = Field(default_factory=list)
    backend_reachable: bool = False
    runtime_loaded: bool = False
    runtime_model: Optional[str] = None
    runtime_status: str = "offline"  # online | offline | no_model | error


class ModelsListOut(BaseModel):
    """List of canonical Syn models (M10 UI)."""

    configured: bool
    models: list[ModelListOut] = Field(default_factory=list)


class BackendListItem(BaseModel):
    """A configured backend (M10 UI)."""

    id: str
    type: str
    reachable: bool
    state: str
    reason: str = ""
    runtime_model: Optional[str] = None
    runtime_models: list[str] = Field(default_factory=list)
    server_version: Optional[str] = None
    last_checked: Optional[str] = None
    endpoint: Optional[str] = None


class BackendsListOut(BaseModel):
    """List of configured backends (M10 UI)."""

    configured: bool
    backends: list[BackendListItem] = Field(default_factory=list)


class OverviewOut(BaseModel):
    """Aggregated overview payload for the M10 UI."""

    syn_healthy: bool
    routing_configured: bool
    routing_mode: str
    admission: dict[str, object]
    backends: list[BackendListItem] = Field(default_factory=list)
    requests: dict[str, int]
    tokens: dict[str, int]
    latency_ms: dict[str, Optional[float]]
    ttft_ms: dict[str, Optional[float]]
    local_inference: dict[str, object] = Field(default_factory=dict)


class SettingsOut(BaseModel):
    """Static, env-derived runtime configuration for the M10 UI settings page."""

    request_size_limit_bytes: Optional[int] = None
    cors_allowed_origins: list[str] = Field(default_factory=list)
    queue_timeout_seconds: Optional[float] = None
    max_active_requests: Optional[int] = None
    max_queued_requests: Optional[int] = None
    admin_auth_configured: bool
    routing_file_path: Optional[str] = None
    note: str = (
        "These values are derived from runtime configuration and "
        "environment. They are read-only from the admin UI."
    )
