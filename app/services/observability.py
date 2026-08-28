"""Observability service (M7).

Provides aggregated metrics, latency statistics, recent request history,
and breakdowns by client/model/status. All queries are read-only and
operate on the existing ``usage_records`` table.

Design notes:
* Percentiles are computed via deterministic sorting over the full
  result set (acceptable for admin-scale queries on a local gateway).
* NULL values are excluded from latency aggregation.
* No prompt or response content is ever stored or returned.
* Timestamps are stored and returned as UTC.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.usage_record import UsageRecord
from app.services.usage import Outcome


# ---- Data classes for aggregated results -----------------------------------


@dataclass(frozen=True)
class LatencyStats:
    """Latency statistics for a single metric dimension."""

    count: int
    avg_ms: Optional[int]
    p50_ms: Optional[int]
    p95_ms: Optional[int]
    max_ms: Optional[int]


@dataclass(frozen=True)
class RequestOutcomeSummary:
    """Counts of requests by outcome status."""

    completed: int
    failed: int
    cancelled: int
    timed_out: int
    rejected: int


@dataclass(frozen=True)
class TokenSummary:
    """Aggregate token counts."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    requests_with_unknown_usage: int


@dataclass(frozen=True)
class SummaryResponse:
    """Full admin observability summary."""

    requests: RequestOutcomeSummary
    tokens: TokenSummary
    latency: LatencyStats
    active: int
    queued: int


@dataclass(frozen=True)
class RecentRequest:
    """A single recent request record for the admin dashboard."""

    request_id: str
    client_id: Optional[str]
    model: str
    streaming: bool
    status: str
    error_code: Optional[str]
    started_at: _dt.datetime
    completed_at: Optional[_dt.datetime]
    total_duration_ms: Optional[int]
    ttft_ms: Optional[int]
    total_tokens: Optional[int]
    queue_wait_ms: Optional[int]


@dataclass(frozen=True)
class ClientBreakdown:
    """Request counts aggregated by client."""

    client_id: Optional[str]
    requests: int
    completed: int
    failed: int
    cancelled: int
    total_tokens: int


@dataclass(frozen=True)
class ModelBreakdown:
    """Request counts aggregated by model."""

    model: str
    requests: int
    completed: int
    failed: int
    cancelled: int
    total_tokens: int


# ---- ObservabilityService ---------------------------------------------------


class ObservabilityService:
    """Read-only aggregation service for admin observability.

    Does not modify any state. All methods operate on a provided DB session.
    """

    def _today_range(
        self, clock: Optional[callable] = None
    ) -> tuple[_dt.datetime, _dt.datetime]:
        """Return (start, end) of today in UTC."""
        now = clock() if clock else _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
        day_start = _dt.datetime(now.year, now.month, now.day)
        day_end = day_start + _dt.timedelta(days=1)
        return day_start, day_end

    def summary(
        self,
        session: Session,
        *,
        since: Optional[_dt.datetime] = None,
        until: Optional[_dt.datetime] = None,
        admission_active: int = 0,
        admission_queued: int = 0,
    ) -> SummaryResponse:
        """Produce a full summary of request outcomes, tokens, and latency."""
        if since is None or until is None:
            since, until = self._today_range()

        rows = (
            session.query(UsageRecord)
            .filter(
                UsageRecord.started_at >= since,
                UsageRecord.started_at < until,
            )
            .all()
        )

        completed = sum(1 for r in rows if r.status == Outcome.COMPLETED)
        failed = sum(1 for r in rows if r.status == Outcome.FAILED)
        cancelled = sum(1 for r in rows if r.status == Outcome.CANCELLED)
        timed_out = sum(1 for r in rows if r.status == Outcome.TIMED_OUT)
        rejected = sum(1 for r in rows if r.status == Outcome.REJECTED)

        prompt_tokens = sum(r.prompt_tokens for r in rows if r.prompt_tokens is not None)
        completion_tokens = sum(
            r.completion_tokens for r in rows if r.completion_tokens is not None
        )
        total_tokens = sum(r.total_tokens for r in rows if r.total_tokens is not None)
        unknown = sum(
            1
            for r in rows
            if r.prompt_tokens is None
            or r.completion_tokens is None
            or r.total_tokens is None
        )

        total_durations = [r.total_duration_ms for r in rows if r.total_duration_ms is not None]

        return SummaryResponse(
            requests=RequestOutcomeSummary(
                completed=completed,
                failed=failed,
                cancelled=cancelled,
                timed_out=timed_out,
                rejected=rejected,
            ),
            tokens=TokenSummary(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                requests_with_unknown_usage=unknown,
            ),
            latency=self._compute_latency(rows),
            active=admission_active,
            queued=admission_queued,
        )

    def latency_stats(
        self,
        session: Session,
        *,
        since: Optional[_dt.datetime] = None,
        until: Optional[_dt.datetime] = None,
    ) -> dict[str, LatencyStats]:
        """Compute latency stats for all measured dimensions."""
        if since is None or until is None:
            since, until = self._today_range()

        rows = (
            session.query(UsageRecord)
            .filter(
                UsageRecord.started_at >= since,
                UsageRecord.started_at < until,
            )
            .all()
        )

        return {
            "total_duration_ms": self._compute_latency_for_field(
                [r.total_duration_ms for r in rows]
            ),
            "queue_wait_ms": self._compute_latency_for_field(
                [r.queue_wait_ms for r in rows]
            ),
            "backend_latency_ms": self._compute_latency_for_field(
                [r.backend_latency_ms for r in rows]
            ),
            "ttft_ms": self._compute_latency_for_field(
                [r.ttft_ms for r in rows]
            ),
            "stream_duration_ms": self._compute_latency_for_field(
                [r.stream_duration_ms for r in rows]
            ),
        }

    def recent_requests(
        self,
        session: Session,
        *,
        limit: int = 50,
        since: Optional[_dt.datetime] = None,
        until: Optional[_dt.datetime] = None,
    ) -> list[RecentRequest]:
        """Return the most recent requests, newest first."""
        limit = min(limit, 200)
        if since is None or until is None:
            since, until = self._today_range()

        rows = (
            session.query(UsageRecord)
            .filter(
                UsageRecord.started_at >= since,
                UsageRecord.started_at < until,
            )
            .order_by(UsageRecord.started_at.desc())
            .limit(limit)
            .all()
        )

        return [
            RecentRequest(
                request_id=r.request_id,
                client_id=r.client_id,
                model=r.model,
                streaming=bool(r.streaming),
                status=r.status,
                error_code=r.error_code,
                started_at=r.started_at,
                completed_at=r.completed_at,
                total_duration_ms=r.total_duration_ms,
                ttft_ms=r.ttft_ms,
                total_tokens=r.total_tokens,
                queue_wait_ms=r.queue_wait_ms,
            )
            for r in rows
        ]

    def client_breakdown(
        self,
        session: Session,
        *,
        since: Optional[_dt.datetime] = None,
        until: Optional[_dt.datetime] = None,
    ) -> list[ClientBreakdown]:
        """Aggregate request counts by client."""
        if since is None or until is None:
            since, until = self._today_range()

        rows = (
            session.query(UsageRecord)
            .filter(
                UsageRecord.started_at >= since,
                UsageRecord.started_at < until,
            )
            .all()
        )

        by_client: dict[Optional[str], list] = {}
        for r in rows:
            by_client.setdefault(r.client_id, []).append(r)

        result = []
        for cid, records in sorted(by_client.items(), key=lambda x: -len(x[1])):
            result.append(
                ClientBreakdown(
                    client_id=cid,
                    requests=len(records),
                    completed=sum(1 for r in records if r.status == Outcome.COMPLETED),
                    failed=sum(1 for r in records if r.status == Outcome.FAILED),
                    cancelled=sum(1 for r in records if r.status == Outcome.CANCELLED),
                    total_tokens=sum(
                        r.total_tokens for r in records if r.total_tokens is not None
                    ),
                )
            )
        return result

    def model_breakdown(
        self,
        session: Session,
        *,
        since: Optional[_dt.datetime] = None,
        until: Optional[_dt.datetime] = None,
    ) -> list[ModelBreakdown]:
        """Aggregate request counts by model."""
        if since is None or until is None:
            since, until = self._today_range()

        rows = (
            session.query(UsageRecord)
            .filter(
                UsageRecord.started_at >= since,
                UsageRecord.started_at < until,
            )
            .all()
        )

        by_model: dict[str, list] = {}
        for r in rows:
            by_model.setdefault(r.model, []).append(r)

        result = []
        for model, records in sorted(by_model.items(), key=lambda x: -len(x[1])):
            result.append(
                ModelBreakdown(
                    model=model,
                    requests=len(records),
                    completed=sum(1 for r in records if r.status == Outcome.COMPLETED),
                    failed=sum(1 for r in records if r.status == Outcome.FAILED),
                    cancelled=sum(1 for r in records if r.status == Outcome.CANCELLED),
                    total_tokens=sum(
                        r.total_tokens for r in records if r.total_tokens is not None
                    ),
                )
            )
        return result

    # ---- Internal helpers ---------------------------------------------------

    def _compute_latency(self, rows: list) -> LatencyStats:
        """Compute latency stats from all rows."""
        durations = [r.total_duration_ms for r in rows if r.total_duration_ms is not None]
        return self._compute_latency_for_field(durations)

    def _compute_latency_for_field(self, values: list) -> LatencyStats:
        """Compute count/avg/p50/p95/max for a list of int values (ms)."""
        clean = sorted(v for v in values if v is not None)
        if not clean:
            return LatencyStats(count=0, avg_ms=None, p50_ms=None, p95_ms=None, max_ms=None)

        count = len(clean)
        avg = round(sum(clean) / count)
        p50 = self._percentile(clean, 0.50)
        p95 = self._percentile(clean, 0.95)
        max_val = clean[-1]

        return LatencyStats(
            count=count,
            avg_ms=avg,
            p50_ms=p50,
            p95_ms=p95,
            max_ms=max_val,
        )

    @staticmethod
    def _percentile(sorted_values: list[int], p: float) -> int:
        """Compute a percentile from a sorted list using linear interpolation.

        For small sample sizes, this returns the nearest-rank value.
        """
        n = len(sorted_values)
        if n == 0:
            return 0
        if n == 1:
            return sorted_values[0]
        # Linear interpolation method
        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_values[int(k)]
        d0 = sorted_values[f] * (c - k)
        d1 = sorted_values[c] * (k - f)
        return round(d0 + d1)
