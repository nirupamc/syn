"""Usage and quota service (M6).

This service is the single place that:
* Resolves the effective policy for an API key
* Checks request-rate and quota pre-conditions
* Records durable usage records
* Aggregates usage summaries for the admin plane

Design notes:
* Daily quota windows are calendar days in UTC. This is simple and
  restart-safe.
* Token quota is **boundary-enforced**: before the request we check
  used_tokens_today >= daily_token_quota. We do NOT estimate the
  upcoming request's tokens. The current request may exceed the quota
  by the cost of one generation.
* Request-rate is enforced by an in-process RateLimiter with a fixed
  window (per minute). The limiter is process-local; it does NOT persist
  across restarts. Daily request quotas, in contrast, are durable.
* The service uses an injectable clock for deterministic tests.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import (
    RateLimitExceededError,
    RequestQuotaExceededError,
    TokenQuotaExceededError,
)
from app.core.policy import EffectivePolicy, resolve_policy
from app.core.rate_limit import RateLimiter, RateLimitResult
from app.logging import get_logger
from app.models.api_key import ApiKey
from app.models.client import Client
from app.models.usage_record import UsageRecord

logger = get_logger("syn.usage")


# ---- Outcomes --------------------------------------------------------------


class Outcome:
    """String constants for usage record outcomes."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"


# ---- Quota usage summary --------------------------------------------------


@dataclass(frozen=True)
class UsageSummary:
    """Aggregated usage for a key/client/period."""

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


# ---- Quota pre-check ------------------------------------------------------


@dataclass(frozen=True)
class QuotaCheckResult:
    """Result of a pre-request quota check."""

    allowed: bool
    rate_limit: Optional[RateLimitResult] = None


class UsageService:
    """Usage tracking, quota enforcement, and usage aggregation."""

    def __init__(
        self,
        rate_limiter: RateLimiter,
        *,
        default_requests_per_minute: int = 0,
        default_requests_per_day: int = 0,
        default_tokens_per_day: int = 0,
        clock: Optional[Callable[[], _dt.datetime]] = None,
    ) -> None:
        self._rate_limiter = rate_limiter
        self._default_rpm = default_requests_per_minute
        self._default_rpd = default_requests_per_day
        self._default_tpd = default_tokens_per_day
        self._clock = clock or (lambda: _dt.datetime.now(_dt.UTC).replace(tzinfo=None))

    def effective_policy(
        self, api_key: ApiKey, client: Client
    ) -> EffectivePolicy:
        return resolve_policy(
            api_key,
            client,
            default_requests_per_minute=self._default_rpm,
            default_requests_per_day=self._default_rpd,
            default_tokens_per_day=self._default_tpd,
        )

    async def precheck(
        self,
        session: Session,
        api_key: ApiKey,
        client: Client,
    ) -> QuotaCheckResult:
        """Pre-request check: rate limit + daily quotas.

        Returns a result with ``allowed=True`` if all checks pass, or
        raises a quota/rate-limit error (which the route layer converts
        to an OpenAI-compatible HTTP response).

        Only admitted inference requests count toward quotas:
        * rate-limit violation raises ``RateLimitExceededError`` (429)
        * request quota violation raises ``RequestQuotaExceededError`` (429)
        * token quota is boundary-enforced (see module docstring)
        """
        policy = self.effective_policy(api_key, client)

        # 1. Rate limit (per-minute, per-key) — in-process limiter
        rate_result = await self._rate_limiter.check(
            key=f"rpm:{api_key.id}",
            limit=policy.requests_per_minute,
        )
        if not rate_result.allowed:
            raise RateLimitExceededError(
                "request rate limit exceeded",
                code="rate_limit_exceeded",
                http_status=429,
            )

        # 2. Daily request quota — durable count
        if policy.requests_per_day > 0:
            used_today = self._count_requests_today(session, api_key.id)
            if used_today >= policy.requests_per_day:
                raise RequestQuotaExceededError(
                    f"daily request quota exceeded "
                    f"({used_today}/{policy.requests_per_day})",
                    code="request_quota_exceeded",
                    http_status=429,
                )

        # 3. Daily token quota — boundary-enforced (pre-check only)
        if policy.tokens_per_day > 0:
            used_tokens = self._sum_tokens_today(session, api_key.id)
            if used_tokens >= policy.tokens_per_day:
                raise TokenQuotaExceededError(
                    f"daily token quota exceeded "
                    f"({used_tokens}/{policy.tokens_per_day})",
                    code="token_quota_exceeded",
                    http_status=429,
                )

        return QuotaCheckResult(allowed=True, rate_limit=rate_result)

    # ---- Usage recording ---------------------------------------------------

    def record(
        self,
        session: Session,
        *,
        request_id: str,
        api_key: Optional[ApiKey],
        client: Optional[Client],
        model: str,
        streaming: bool,
        started_at: _dt.datetime,
        completed_at: _dt.datetime,
        outcome: str,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        queue_wait_ms: Optional[int] = None,
        error_code: Optional[str] = None,
        backend_latency_ms: Optional[int] = None,
        ttft_ms: Optional[int] = None,
        stream_duration_ms: Optional[int] = None,
        total_duration_ms: Optional[int] = None,
    ) -> UsageRecord:
        """Persist a usage record.

        Token fields may be None (not reliably known) for cancelled or
        failed requests. The record is committed immediately.
        """
        user_id = client.user_id if client is not None else None
        record = UsageRecord(
            request_id=request_id,
            user_id=user_id,
            client_id=client.id if client is not None else None,
            api_key_id=api_key.id if api_key is not None else None,
            model=model,
            streaming=1 if streaming else 0,
            started_at=started_at,
            completed_at=completed_at,
            status=outcome,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            queue_wait_ms=queue_wait_ms,
            error_code=error_code,
            backend_latency_ms=backend_latency_ms,
            ttft_ms=ttft_ms,
            stream_duration_ms=stream_duration_ms,
            total_duration_ms=total_duration_ms,
        )
        session.add(record)
        session.commit()
        return record

    # ---- Usage aggregation -----------------------------------------------

    def summarize(
        self,
        session: Session,
        *,
        api_key_id: Optional[str] = None,
        client_id: Optional[str] = None,
        since: Optional[_dt.datetime] = None,
        until: Optional[_dt.datetime] = None,
    ) -> UsageSummary:
        """Aggregate usage for a key or client within a time window.

        If both ``api_key_id`` and ``client_id`` are None, aggregates
        across all keys. Time bounds default to "today UTC" if not
        provided.
        """
        if since is None or until is None:
            now = self._clock()
            day_start = _dt.datetime(now.year, now.month, now.day)
            since = since or day_start
            until = until or day_start + _dt.timedelta(days=1)

        q = session.query(UsageRecord).filter(
            UsageRecord.started_at >= since,
            UsageRecord.started_at < until,
        )
        if api_key_id is not None:
            q = q.filter(UsageRecord.api_key_id == api_key_id)
        elif client_id is not None:
            q = q.filter(UsageRecord.client_id == client_id)

        rows = q.all()

        requests = len(rows)
        successful = sum(1 for r in rows if r.status == Outcome.COMPLETED)
        failed = sum(1 for r in rows if r.status == Outcome.FAILED)
        cancelled = sum(1 for r in rows if r.status == Outcome.CANCELLED)
        rejected = sum(1 for r in rows if r.status == Outcome.REJECTED)
        timed_out = sum(1 for r in rows if r.status == Outcome.TIMED_OUT)

        # Token sums: treat NULL as unknown (not as 0)
        prompt_tokens = sum(
            r.prompt_tokens for r in rows if r.prompt_tokens is not None
        )
        completion_tokens = sum(
            r.completion_tokens for r in rows if r.completion_tokens is not None
        )
        total_tokens = sum(
            r.total_tokens for r in rows if r.total_tokens is not None
        )
        # Count records where any of the token fields is NULL
        unknown = sum(
            1
            for r in rows
            if r.prompt_tokens is None
            or r.completion_tokens is None
            or r.total_tokens is None
        )

        return UsageSummary(
            requests=requests,
            successful_requests=successful,
            failed_requests=failed,
            cancelled_requests=cancelled,
            rejected_requests=rejected,
            timed_out_requests=timed_out,
            requests_with_unknown_usage=unknown,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    # ---- Internal helpers --------------------------------------------------

    def _count_requests_today(self, session: Session, api_key_id: str) -> int:
        """Count requests for a key in the current UTC day."""
        now = self._clock()
        day_start = _dt.datetime(now.year, now.month, now.day)
        day_end = day_start + _dt.timedelta(days=1)
        return (
            session.query(func.count(UsageRecord.id))
            .filter(
                UsageRecord.api_key_id == api_key_id,
                UsageRecord.started_at >= day_start,
                UsageRecord.started_at < day_end,
            )
            .scalar()
            or 0
        )

    def _sum_tokens_today(self, session: Session, api_key_id: str) -> int:
        """Sum total_tokens for a key in the current UTC day.

        Only counts records with non-null total_tokens (i.e. we have
        reliable usage data).
        """
        now = self._clock()
        day_start = _dt.datetime(now.year, now.month, now.day)
        day_end = day_start + _dt.timedelta(days=1)
        result = (
            session.query(func.coalesce(func.sum(UsageRecord.total_tokens), 0))
            .filter(
                UsageRecord.api_key_id == api_key_id,
                UsageRecord.started_at >= day_start,
                UsageRecord.started_at < day_end,
                UsageRecord.total_tokens.isnot(None),
            )
            .scalar()
        )
        return int(result or 0)
