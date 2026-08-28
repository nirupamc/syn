"""M7 tests: Observability service, admin API, dashboard, telemetry.

Tests use isolated DBs and deterministic seeded records. No GPU required.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.admission import AdmissionController
from app.core.rate_limit import RateLimiter
from app.db import Database
from app.models.usage_record import UsageRecord
from app.services.observability import ObservabilityService
from app.services.usage import Outcome, UsageService


# ---- helpers ---------------------------------------------------------------


def _seed_records(
    session,
    *,
    count: int = 10,
    model: str = "test-model",
    client_id: Optional[str] = None,
    api_key_id: Optional[str] = None,
    status: str = Outcome.COMPLETED,
    streaming: bool = False,
    base_time: Optional[_dt.datetime] = None,
    total_duration_ms: Optional[int] = 100,
    ttft_ms: Optional[int] = None,
    queue_wait_ms: Optional[int] = None,
    backend_latency_ms: Optional[int] = None,
    prompt_tokens: Optional[int] = 10,
    completion_tokens: Optional[int] = 20,
) -> tuple[list[UsageRecord], _dt.datetime, _dt.datetime]:
    """Insert deterministic test records.

    Returns (records, min_started_at, max_completed_at) for use in queries.
    """
    if base_time is None:
        base_time = _dt.datetime(2026, 1, 15, 12, 0, 0)
    records = []
    min_time = base_time
    max_time = base_time
    for i in range(count):
        started = base_time + _dt.timedelta(seconds=i)
        completed = started + _dt.timedelta(milliseconds=total_duration_ms or 0)
        if started < min_time:
            min_time = started
        if completed > max_time:
            max_time = completed
        record = UsageRecord(
            request_id=f"req-{i:04d}",
            client_id=client_id,
            api_key_id=api_key_id,
            model=model,
            streaming=1 if streaming else 0,
            started_at=started,
            completed_at=completed,
            status=status,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=(
                (prompt_tokens or 0) + (completion_tokens or 0)
                if prompt_tokens is not None and completion_tokens is not None
                else None
            ),
            queue_wait_ms=queue_wait_ms,
            error_code=None,
            backend_latency_ms=backend_latency_ms,
            ttft_ms=ttft_ms,
            stream_duration_ms=total_duration_ms,
            total_duration_ms=total_duration_ms,
        )
        session.add(record)
        records.append(record)
    session.commit()
    return records, min_time, max_time + _dt.timedelta(days=1)


def _build_test_app(tmp_path):
    """Create a minimal app for admin API testing."""
    db_path = tmp_path / "obs_test.db"
    settings = Settings(
        app_name="Syn",
        app_version="0.1.0",
        environment="testing",
        host="127.0.0.1",
        port=8001,
        database_url=f"sqlite:///{db_path}",
        log_level="INFO",
        backend_type="llama_cpp",
        backend_base_url="http://127.0.0.1:59999",
        backend_timeout_seconds=5.0,
        backend_connect_timeout_seconds=1.0,
        backend_health_timeout_seconds=1.0,
        admin_secret="test-admin-secret",
    )
    from app.main import create_app

    test_app = create_app(settings)

    database = Database(settings.database_url)
    database.connect()
    import app.models  # noqa: F401
    from app.db.base import Base

    Base.metadata.create_all(bind=database.engine)

    # Wire services
    rate_limiter = RateLimiter(window_seconds=60)
    usage_svc = UsageService(rate_limiter)
    test_app.state.database = database
    test_app.state.usage_service = usage_svc
    test_app.state.observability_service = ObservabilityService()

    admission = AdmissionController(
        max_active_requests=2,
        max_queue_size=4,
        queue_timeout_seconds=30.0,
    )
    test_app.state.admission = admission

    return test_app, database


# ---- ObservabilityService unit tests ----------------------------------------


def test_summary_empty(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    obs = ObservabilityService()
    session = database.session_factory()
    try:
        summary = obs.summary(session, admission_active=0, admission_queued=0)
        assert summary.requests.completed == 0
        assert summary.requests.failed == 0
        assert summary.requests.cancelled == 0
        assert summary.requests.timed_out == 0
        assert summary.requests.rejected == 0
        assert summary.tokens.total_tokens == 0
        assert summary.latency.count == 0
        assert summary.latency.avg_ms is None
        assert summary.latency.p50_ms is None
        assert summary.latency.p95_ms is None
        assert summary.latency.max_ms is None
        assert summary.active == 0
        assert summary.queued == 0
    finally:
        session.close()
        database.dispose()


def test_summary_with_records(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        r1, t1_start, t1_end = _seed_records(session, count=5, status=Outcome.COMPLETED, total_duration_ms=100)
        r2, t2_start, t2_end = _seed_records(
            session,
            count=2,
            model="other-model",
            status=Outcome.FAILED,
            total_duration_ms=200,
            base_time=_dt.datetime(2026, 1, 15, 12, 1, 0),
        )
        r3, t3_start, t3_end = _seed_records(
            session,
            count=1,
            status=Outcome.CANCELLED,
            total_duration_ms=None,
            prompt_tokens=None,
            completion_tokens=None,
            base_time=_dt.datetime(2026, 1, 15, 12, 2, 0),
        )

        # Use explicit time range covering all records
        since = min(t1_start, t2_start, t3_start) - _dt.timedelta(hours=1)
        until = max(t1_end, t2_end, t3_end) + _dt.timedelta(hours=1)

        obs = ObservabilityService()
        summary = obs.summary(session, since=since, until=until, admission_active=1, admission_queued=2)

        assert summary.requests.completed == 5
        assert summary.requests.failed == 2
        assert summary.requests.cancelled == 1
        assert summary.tokens.prompt_tokens == 50 + 20  # 5*10 + 2*10
        assert summary.active == 1
        assert summary.queued == 2
        assert summary.latency.count == 7  # only records with non-null duration
        assert summary.latency.avg_ms is not None
        assert summary.latency.p50_ms is not None
        assert summary.latency.p95_ms is not None
        assert summary.latency.max_ms is not None
    finally:
        session.close()
        database.dispose()


def test_latency_stats_all_dimensions(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        r1, t1_start, t1_end = _seed_records(
            session,
            count=3,
            total_duration_ms=100,
            ttft_ms=50,
            backend_latency_ms=80,
            queue_wait_ms=10,
        )
        r2, t2_start, t2_end = _seed_records(
            session,
            count=2,
            model="fast-model",
            total_duration_ms=200,
            ttft_ms=100,
            backend_latency_ms=150,
            queue_wait_ms=0,
            base_time=_dt.datetime(2026, 1, 15, 12, 1, 0),
        )

        since = min(t1_start, t2_start) - _dt.timedelta(hours=1)
        until = max(t1_end, t2_end) + _dt.timedelta(hours=1)

        obs = ObservabilityService()
        stats = obs.latency_stats(session, since=since, until=until)

        assert "total_duration_ms" in stats
        assert "queue_wait_ms" in stats
        assert "backend_latency_ms" in stats
        assert "ttft_ms" in stats
        assert "stream_duration_ms" in stats

        total = stats["total_duration_ms"]
        assert total.count == 5
        assert total.avg_ms == 140  # (100*3 + 200*2) / 5
        assert total.max_ms == 200

        ttft = stats["ttft_ms"]
        assert ttft.count == 5
        assert ttft.avg_ms == 70  # (50*3 + 100*2) / 5
    finally:
        session.close()
        database.dispose()


def test_recent_requests_ordering(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        r1, t1s, t1e = _seed_records(session, count=3, base_time=_dt.datetime(2026, 1, 15, 12, 0, 0))
        r2, t2s, t2e = _seed_records(
            session,
            count=2,
            model="late-model",
            base_time=_dt.datetime(2026, 1, 15, 12, 5, 0),
        )

        since = min(t1s, t2s) - _dt.timedelta(hours=1)
        until = max(t1e, t2e) + _dt.timedelta(hours=1)

        obs = ObservabilityService()
        recent = obs.recent_requests(session, limit=5, since=since, until=until)

        assert len(recent) == 5
        # Newest first: late-model req-0001 (12:05:01), late-model req-0000 (12:05:00)
        assert recent[0].model == "late-model"
        assert recent[0].request_id == "req-0001"
        assert recent[-1].model == "test-model"
    finally:
        session.close()
        database.dispose()


def test_recent_requests_limit(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        _, ts, te = _seed_records(session, count=10)
        since = ts - _dt.timedelta(hours=1)
        until = te + _dt.timedelta(hours=1)
        obs = ObservabilityService()
        recent = obs.recent_requests(session, limit=3, since=since, until=until)
        assert len(recent) == 3
    finally:
        session.close()
        database.dispose()


def test_client_breakdown(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        r1, t1s, t1e = _seed_records(session, count=5, client_id="client-a")
        r2, t2s, t2e = _seed_records(
            session,
            count=3,
            client_id="client-b",
            base_time=_dt.datetime(2026, 1, 15, 12, 1, 0),
        )

        since = min(t1s, t2s) - _dt.timedelta(hours=1)
        until = max(t1e, t2e) + _dt.timedelta(hours=1)

        obs = ObservabilityService()
        breakdown = obs.client_breakdown(session, since=since, until=until)

        assert len(breakdown) == 2
        # client-a has more requests
        assert breakdown[0].client_id == "client-a"
        assert breakdown[0].requests == 5
        assert breakdown[1].client_id == "client-b"
        assert breakdown[1].requests == 3
    finally:
        session.close()
        database.dispose()


def test_model_breakdown(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        r1, t1s, t1e = _seed_records(session, count=8, model="model-a")
        r2, t2s, t2e = _seed_records(
            session, count=2, model="model-b",
            base_time=_dt.datetime(2026, 1, 15, 12, 1, 0),
        )

        since = min(t1s, t2s) - _dt.timedelta(hours=1)
        until = max(t1e, t2e) + _dt.timedelta(hours=1)

        obs = ObservabilityService()
        breakdown = obs.model_breakdown(session, since=since, until=until)

        assert len(breakdown) == 2
        assert breakdown[0].model == "model-a"
        assert breakdown[0].requests == 8
        assert breakdown[1].model == "model-b"
        assert breakdown[1].requests == 2
    finally:
        session.close()
        database.dispose()


def test_percentile_small_sample(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        _, ts, te = _seed_records(session, count=1, total_duration_ms=100)
        since = ts - _dt.timedelta(hours=1)
        until = te + _dt.timedelta(hours=1)
        obs = ObservabilityService()
        stats = obs.latency_stats(session, since=since, until=until)
        assert stats["total_duration_ms"].p50_ms == 100
        assert stats["total_duration_ms"].p95_ms == 100
        assert stats["total_duration_ms"].avg_ms == 100
    finally:
        session.close()
        database.dispose()


def test_unknown_usage_counted(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        r1, t1s, t1e = _seed_records(
            session, count=3, prompt_tokens=None, completion_tokens=None,
        )
        r2, t2s, t2e = _seed_records(
            session, count=2, prompt_tokens=10, completion_tokens=20,
            base_time=_dt.datetime(2026, 1, 15, 12, 1, 0),
        )

        since = min(t1s, t2s) - _dt.timedelta(hours=1)
        until = max(t1e, t2e) + _dt.timedelta(hours=1)

        obs = ObservabilityService()
        summary = obs.summary(session, since=since, until=until)
        assert summary.tokens.requests_with_unknown_usage == 3
    finally:
        session.close()
        database.dispose()


# ---- Admin API security tests ----------------------------------------------


def test_admin_observability_summary_requires_auth(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get("/admin/observability/summary")
    assert resp.status_code == 401


def test_admin_observability_summary_wrong_secret(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get(
        "/admin/observability/summary",
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert resp.status_code == 401


def test_admin_observability_summary_ok(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get(
        "/admin/observability/summary",
        headers={"Authorization": "Bearer test-admin-secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "requests" in body
    assert "tokens" in body
    assert "latency" in body
    assert "active" in body
    assert "queued" in body


def test_admin_observability_latency_ok(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get(
        "/admin/observability/latency",
        headers={"Authorization": "Bearer test-admin-secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "total_duration_ms" in body
    assert "queue_wait_ms" in body
    assert "backend_latency_ms" in body
    assert "ttft_ms" in body
    assert "stream_duration_ms" in body


def test_admin_observability_recent_ok(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get(
        "/admin/observability/recent",
        headers={"Authorization": "Bearer test-admin-secret"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_admin_observability_clients_ok(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get(
        "/admin/observability/clients",
        headers={"Authorization": "Bearer test-admin-secret"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_admin_observability_models_ok(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get(
        "/admin/observability/models",
        headers={"Authorization": "Bearer test-admin-secret"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_inference_key_cannot_access_observability(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        from app.services import admin as admin_service

        user = admin_service.create_user(session, "test-user")
        client_obj = admin_service.create_client(
            session, user_id=user.id, name="test-client"
        )
        _, full_token = admin_service.create_api_key(
            session, client_id=client_obj.id, name="test-key"
        )
    finally:
        session.close()

    client = TestClient(test_app)
    resp = client.get(
        "/admin/observability/summary",
        headers={"Authorization": f"Bearer {full_token}"},
    )
    assert resp.status_code == 401


def test_dashboard_requires_admin_auth(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get("/admin/dashboard")
    assert resp.status_code == 401


def test_dashboard_returns_html(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get(
        "/admin/dashboard",
        headers={"Authorization": "Bearer test-admin-secret"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "Syn Admin Dashboard" in html
    assert "Completed" in html
    assert "Backend" in html


def test_dashboard_no_secrets(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get(
        "/admin/dashboard",
        headers={"Authorization": "Bearer test-admin-secret"},
    )
    assert resp.status_code == 200
    html = resp.text
    assert "test-admin-secret" not in html
    assert "Bearer" not in html


def test_dashboard_no_prompt_content(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get(
        "/admin/dashboard",
        headers={"Authorization": "Bearer test-admin-secret"},
    )
    assert resp.status_code == 200
    html = resp.text
    assert "prompt content" not in html.lower()


# ---- Prometheus metrics endpoint -------------------------------------------


def test_metrics_requires_admin_auth(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get("/admin/metrics")
    assert resp.status_code == 401


def test_metrics_returns_text(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get(
        "/admin/metrics",
        headers={"Authorization": "Bearer test-admin-secret"},
    )
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "syn_requests_total" in body
    assert "syn_active_requests" in body
    assert "syn_queued_requests" in body
    assert "syn_tokens_total" in body
    assert "syn_request_duration_seconds" in body
    assert "syn_ttft_seconds" in body


def test_metrics_no_high_cardinality_labels(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    client = TestClient(test_app)
    resp = client.get(
        "/admin/metrics",
        headers={"Authorization": "Bearer test-admin-secret"},
    )
    body = resp.text
    assert "request_id" not in body
    assert "api_key_id" not in body
    assert "user_id" not in body


# ---- M7 telemetry in usage records ----------------------------------------


def test_usage_record_has_telemetry_columns(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        records, _, _ = _seed_records(
            session,
            count=1,
            total_duration_ms=150,
            ttft_ms=50,
            backend_latency_ms=120,
            queue_wait_ms=10,
        )
        record = records[0]
        assert record.total_duration_ms == 150
        assert record.ttft_ms == 50
        assert record.backend_latency_ms == 120
        assert record.queue_wait_ms == 10
        assert record.stream_duration_ms == 150
    finally:
        session.close()
        database.dispose()


def test_usage_record_telemetry_nullable(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        records, _, _ = _seed_records(
            session,
            count=1,
            total_duration_ms=None,
            ttft_ms=None,
            backend_latency_ms=None,
            queue_wait_ms=None,
        )
        record = records[0]
        assert record.total_duration_ms is None
        assert record.ttft_ms is None
        assert record.backend_latency_ms is None
        assert record.queue_wait_ms is None
    finally:
        session.close()
        database.dispose()


# ---- Summary with admission state -----------------------------------------


def test_summary_reflects_admission_state(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        obs = ObservabilityService()
        summary = obs.summary(session, admission_active=3, admission_queued=5)
        assert summary.active == 3
        assert summary.queued == 5
    finally:
        session.close()
        database.dispose()


# ---- Outcome counts in aggregates -----------------------------------------


def test_outcome_counts_accurate(tmp_path):
    test_app, database = _build_test_app(tmp_path)
    session = database.session_factory()
    try:
        r1, t1s, t1e = _seed_records(session, count=10, status=Outcome.COMPLETED)
        r2, t2s, t2e = _seed_records(session, count=3, status=Outcome.FAILED, base_time=_dt.datetime(2026, 1, 15, 12, 1, 0))
        r3, t3s, t3e = _seed_records(session, count=2, status=Outcome.CANCELLED, base_time=_dt.datetime(2026, 1, 15, 12, 2, 0))
        r4, t4s, t4e = _seed_records(session, count=1, status=Outcome.TIMED_OUT, base_time=_dt.datetime(2026, 1, 15, 12, 3, 0))
        r5, t5s, t5e = _seed_records(session, count=1, status=Outcome.REJECTED, base_time=_dt.datetime(2026, 1, 15, 12, 4, 0))

        since = min(t1s, t2s, t3s, t4s, t5s) - _dt.timedelta(hours=1)
        until = max(t1e, t2e, t3e, t4e, t5e) + _dt.timedelta(hours=1)

        obs = ObservabilityService()
        summary = obs.summary(session, since=since, until=until)
        assert summary.requests.completed == 10
        assert summary.requests.failed == 3
        assert summary.requests.cancelled == 2
        assert summary.requests.timed_out == 1
        assert summary.requests.rejected == 1
        assert summary.requests.completed + summary.requests.failed + summary.requests.cancelled + summary.requests.timed_out + summary.requests.rejected == 17
    finally:
        session.close()
        database.dispose()


# ---- Latency percentile edge cases -----------------------------------------


def test_percentile_even_count():
    obs = ObservabilityService()
    result = obs._percentile([1, 2], 0.50)
    # Linear interpolation: 1*(1-0.5) + 2*(0.5) = 1.5, rounded = 2
    assert result == 2


def test_percentile_odd_count():
    obs = ObservabilityService()
    result = obs._percentile([1, 2, 3], 0.50)
    assert result == 2


def test_percentile_empty():
    obs = ObservabilityService()
    result = obs._percentile([], 0.50)
    assert result == 0


def test_percentile_single():
    obs = ObservabilityService()
    result = obs._percentile([42], 0.95)
    assert result == 42
