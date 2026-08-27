"""M6 tests: usage service (recording, quota checks, summarization)."""

from __future__ import annotations

import datetime as _dt

import pytest

from app.core.rate_limit import RateLimiter
from app.db import Database
from app.services.usage import Outcome, UsageService


@pytest.fixture
def db_with_tables(tmp_path):
    """Create a fresh database with M3/M6 tables."""
    import app.models  # noqa: F401
    from app.db.base import Base

    db = Database(f"sqlite:///{tmp_path}/test.db")
    db.connect()
    Base.metadata.create_all(bind=db.engine)
    yield db
    db.dispose()


def _create_test_data(db):
    """Create a test user, client, and API key."""
    from app.services import admin as admin_service

    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "test-user")
        client_obj = admin_service.create_client(session, user_id=user.id, name="c")
        api_key, _ = admin_service.create_api_key(
            session, client_id=client_obj.id, name="k"
        )
        return user, client_obj, api_key
    finally:
        session.close()


def _fixed_clock(at: _dt.datetime):
    """Return a clock function that always returns the given time."""
    def clock():
        return at
    return clock


# ---- recording -------------------------------------------------------------


def test_record_completed_usage(db_with_tables):
    user, client, key = _create_test_data(db_with_tables)
    svc = UsageService(RateLimiter(), clock=_fixed_clock(_dt.datetime(2026, 1, 15, 12, 0, 0)))

    session = db_with_tables.session_factory()
    try:
        svc.record(
            session,
            request_id="r1",
            api_key=key,
            client=client,
            model="test-model",
            streaming=False,
            started_at=_dt.datetime(2026, 1, 15, 12, 0, 0),
            completed_at=_dt.datetime(2026, 1, 15, 12, 0, 1),
            outcome=Outcome.COMPLETED,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )
    finally:
        session.close()

    # Verify by reading back
    session = db_with_tables.session_factory()
    try:
        from app.models.usage_record import UsageRecord

        records = session.query(UsageRecord).all()
        assert len(records) == 1
        r = records[0]
        assert r.request_id == "r1"
        assert r.api_key_id == key.id
        assert r.client_id == client.id
        assert r.model == "test-model"
        assert r.streaming == 0
        assert r.status == "completed"
        assert r.prompt_tokens == 10
        assert r.completion_tokens == 5
        assert r.total_tokens == 15
    finally:
        session.close()


def test_record_cancelled_stream_no_fabricated_tokens(db_with_tables):
    user, client, key = _create_test_data(db_with_tables)
    svc = UsageService(RateLimiter(), clock=_fixed_clock(_dt.datetime(2026, 1, 15, 12, 0, 0)))

    session = db_with_tables.session_factory()
    try:
        svc.record(
            session,
            request_id="r2",
            api_key=key,
            client=client,
            model="test-model",
            streaming=True,
            started_at=_dt.datetime(2026, 1, 15, 12, 0, 0),
            completed_at=_dt.datetime(2026, 1, 15, 12, 0, 5),
            outcome=Outcome.CANCELLED,
            # Token counts are None — we don't know them for cancelled streams
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )
    finally:
        session.close()

    session = db_with_tables.session_factory()
    try:
        from app.models.usage_record import UsageRecord

        r = session.query(UsageRecord).filter_by(request_id="r2").one()
        assert r.status == "cancelled"
        assert r.prompt_tokens is None
        assert r.completion_tokens is None
        assert r.total_tokens is None
        assert r.streaming == 1
    finally:
        session.close()


def test_record_failed_request(db_with_tables):
    user, client, key = _create_test_data(db_with_tables)
    svc = UsageService(RateLimiter(), clock=_fixed_clock(_dt.datetime(2026, 1, 15, 12, 0, 0)))

    session = db_with_tables.session_factory()
    try:
        svc.record(
            session,
            request_id="r3",
            api_key=key,
            client=client,
            model="test-model",
            streaming=False,
            started_at=_dt.datetime(2026, 1, 15, 12, 0, 0),
            completed_at=_dt.datetime(2026, 1, 15, 12, 0, 2),
            outcome=Outcome.FAILED,
            error_code="backend_timeout",
        )
    finally:
        session.close()

    session = db_with_tables.session_factory()
    try:
        from app.models.usage_record import UsageRecord

        r = session.query(UsageRecord).filter_by(request_id="r3").one()
        assert r.status == "failed"
        assert r.error_code == "backend_timeout"
    finally:
        session.close()


# ---- privacy: no prompt/messages/keys in DB -------------------------------


def test_usage_record_contains_no_prompt_content(db_with_tables):
    """Usage records must never contain prompt text, messages, or keys."""
    user, client, key = _create_test_data(db_with_tables)
    svc = UsageService(RateLimiter(), clock=_fixed_clock(_dt.datetime(2026, 1, 15, 12, 0, 0)))

    # Record a "completion" with intentionally poisoned "prompt" data
    # that we should never see in the DB
    session = db_with_tables.session_factory()
    try:
        record = svc.record(
            session,
            request_id="r4",
            api_key=key,
            client=client,
            model="test-model",
            streaming=False,
            started_at=_dt.datetime(2026, 1, 15, 12, 0, 0),
            completed_at=_dt.datetime(2026, 1, 15, 12, 0, 1),
            outcome=Outcome.COMPLETED,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )
        # Add a comment with a fake "prompt" field via setattr
        # (this is just to verify the column doesn't exist)
        try:
            record.prompt = "SECRET PROMPT TEXT"
            record.key_hash = "SECRET KEY HASH"
        except Exception:
            pass
    finally:
        session.close()

    # Read back and verify NO prompt/key content is stored
    session = db_with_tables.session_factory()
    try:
        from app.models.usage_record import UsageRecord

        r = session.query(UsageRecord).filter_by(request_id="r4").one()
        # Check the actual column names
        column_names = {c.name for c in r.__table__.columns}
        # Must NOT have prompt, messages, key, secret columns
        forbidden = {"prompt", "messages", "key", "secret", "key_hash", "api_key"}
        leaked = forbidden & column_names
        assert not leaked, f"UsageRecord leaks forbidden columns: {leaked}"
    finally:
        session.close()


# ---- precheck: rate limit -------------------------------------------------


async def test_precheck_rate_limit_blocks(db_with_tables):
    user, client, key = _create_test_data(db_with_tables)
    limiter = RateLimiter(window_seconds=60, clock=lambda: 0.0)
    svc = UsageService(limiter, default_requests_per_minute=2)

    session = db_with_tables.session_factory()
    try:
        # First two allowed
        await svc.precheck(session, key, client)
        await svc.precheck(session, key, client)
        # Third should raise
        from app.core.errors import RateLimitExceededError

        with pytest.raises(RateLimitExceededError) as exc:
            await svc.precheck(session, key, client)
        assert exc.value.code == "rate_limit_exceeded"
        assert exc.value.http_status == 429
    finally:
        session.close()


# ---- precheck: request quota -----------------------------------------------


async def test_precheck_request_quota_blocks(db_with_tables):
    user, client, key = _create_test_data(db_with_tables)
    limiter = RateLimiter(window_seconds=60, clock=lambda: 0.0)
    # Use a fixed clock at the day we want to record in
    fixed_now = _dt.datetime(2026, 1, 15, 12, 0, 0)
    svc = UsageService(
        limiter,
        default_requests_per_day=2,
        clock=_fixed_clock(fixed_now),
    )

    # Pre-record 2 completed requests
    session = db_with_tables.session_factory()
    try:
        for i in range(2):
            svc.record(
                session,
                request_id=f"r{i}",
                api_key=key,
                client=client,
                model="m",
                streaming=False,
                started_at=fixed_now,
                completed_at=fixed_now,
                outcome=Outcome.COMPLETED,
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            )
    finally:
        session.close()

    # Third should be rejected
    from app.core.errors import RequestQuotaExceededError

    session = db_with_tables.session_factory()
    try:
        with pytest.raises(RequestQuotaExceededError) as exc:
            await svc.precheck(session, key, client)
        assert exc.value.code == "request_quota_exceeded"
    finally:
        session.close()


# ---- precheck: token quota (boundary-enforced) -----------------------------


async def test_precheck_token_quota_blocks(db_with_tables):
    user, client, key = _create_test_data(db_with_tables)
    limiter = RateLimiter(window_seconds=60, clock=lambda: 0.0)
    fixed_now = _dt.datetime(2026, 1, 15, 12, 0, 0)
    svc = UsageService(
        limiter,
        default_tokens_per_day=100,
        clock=_fixed_clock(fixed_now),
    )

    # Pre-record 100 tokens of usage
    session = db_with_tables.session_factory()
    try:
        svc.record(
            session,
            request_id="r1",
            api_key=key,
            client=client,
            model="m",
            streaming=False,
            started_at=fixed_now,
            completed_at=fixed_now,
            outcome=Outcome.COMPLETED,
            prompt_tokens=50,
            completion_tokens=50,
            total_tokens=100,
        )
    finally:
        session.close()

    # Next request should be rejected (boundary-enforced)
    from app.core.errors import TokenQuotaExceededError

    session = db_with_tables.session_factory()
    try:
        with pytest.raises(TokenQuotaExceededError) as exc:
            await svc.precheck(session, key, client)
        assert exc.value.code == "token_quota_exceeded"
    finally:
        session.close()


# ---- precheck: unlimited passes -------------------------------------------


async def test_precheck_unlimited_passes(db_with_tables):
    user, client, key = _create_test_data(db_with_tables)
    limiter = RateLimiter(window_seconds=60, clock=lambda: 0.0)
    svc = UsageService(limiter)  # all defaults = unlimited (0)

    session = db_with_tables.session_factory()
    try:
        # Should never raise
        for _ in range(100):
            await svc.precheck(session, key, client)
    finally:
        session.close()


# ---- summarization --------------------------------------------------------


def test_summarize_basic(db_with_tables):
    user, client, key = _create_test_data(db_with_tables)
    svc = UsageService(RateLimiter(), clock=_fixed_clock(_dt.datetime(2026, 1, 15, 12, 0, 0)))

    # Record 3 completed + 1 failed
    session = db_with_tables.session_factory()
    try:
        for i, (outcome, p, c) in enumerate([
            (Outcome.COMPLETED, 10, 5),
            (Outcome.COMPLETED, 20, 10),
            (Outcome.COMPLETED, 30, 15),
            (Outcome.FAILED, 0, 0),
        ]):
            svc.record(
                session,
                request_id=f"r{i}",
                api_key=key,
                client=client,
                model="m",
                streaming=False,
                started_at=_dt.datetime(2026, 1, 15, 12, 0, 0),
                completed_at=_dt.datetime(2026, 1, 15, 12, 0, 1),
                outcome=outcome,
                prompt_tokens=p or None,
                completion_tokens=c or None,
                total_tokens=(p + c) if p and c else None,
            )
    finally:
        session.close()

    session = db_with_tables.session_factory()
    try:
        summary = svc.summarize(session, api_key_id=key.id)
        assert summary.requests == 4
        assert summary.successful_requests == 3
        assert summary.failed_requests == 1
        assert summary.prompt_tokens == 60
        assert summary.completion_tokens == 30
        assert summary.total_tokens == 90
        # The failed record has null tokens, so it's unknown
        assert summary.requests_with_unknown_usage == 1
    finally:
        session.close()


def test_summarize_filters_by_key(db_with_tables):
    user, client, key1 = _create_test_data(db_with_tables)
    # Create a second key for the same client
    from app.services import admin as admin_service

    session = db_with_tables.session_factory()
    try:
        key2, _ = admin_service.create_api_key(
            session, client_id=client.id, name="k2"
        )
    finally:
        session.close()

    svc = UsageService(RateLimiter(), clock=_fixed_clock(_dt.datetime(2026, 1, 15, 12, 0, 0)))

    session = db_with_tables.session_factory()
    try:
        # Record 2 for key1, 3 for key2
        for i in range(2):
            svc.record(
                session,
                request_id=f"a{i}",
                api_key=key1,
                client=client,
                model="m",
                streaming=False,
                started_at=_dt.datetime(2026, 1, 15, 12, 0, 0),
                completed_at=_dt.datetime(2026, 1, 15, 12, 0, 1),
                outcome=Outcome.COMPLETED,
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            )
        for i in range(3):
            svc.record(
                session,
                request_id=f"b{i}",
                api_key=key2,
                client=client,
                model="m",
                streaming=False,
                started_at=_dt.datetime(2026, 1, 15, 12, 0, 0),
                completed_at=_dt.datetime(2026, 1, 15, 12, 0, 1),
                outcome=Outcome.COMPLETED,
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            )
    finally:
        session.close()

    session = db_with_tables.session_factory()
    try:
        s1 = svc.summarize(session, api_key_id=key1.id)
        s2 = svc.summarize(session, api_key_id=key2.id)
        assert s1.requests == 2
        assert s2.requests == 3
    finally:
        session.close()


# ---- restart durability: usage persists across "restart" -----------------


def test_usage_persists_across_db_reopen(tmp_path):
    """Simulate a restart: close DB, reopen, verify usage is still there."""
    import app.models  # noqa: F401
    from app.db.base import Base

    db_path = tmp_path / "test.db"
    db1 = Database(f"sqlite:///{db_path}")
    db1.connect()
    Base.metadata.create_all(bind=db1.engine)

    user, client, key = _create_test_data(db1)
    svc = UsageService(RateLimiter(), clock=_fixed_clock(_dt.datetime(2026, 1, 15, 12, 0, 0)))

    session = db1.session_factory()
    try:
        svc.record(
            session,
            request_id="r1",
            api_key=key,
            client=client,
            model="m",
            streaming=False,
            started_at=_dt.datetime(2026, 1, 15, 12, 0, 0),
            completed_at=_dt.datetime(2026, 1, 15, 12, 0, 1),
            outcome=Outcome.COMPLETED,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
    finally:
        session.close()
    db1.dispose()

    # Simulate restart: open a new Database instance on the same file
    db2 = Database(f"sqlite:///{db_path}")
    db2.connect()
    svc2 = UsageService(RateLimiter(), clock=_fixed_clock(_dt.datetime(2026, 1, 15, 12, 0, 0)))

    session = db2.session_factory()
    try:
        summary = svc2.summarize(session, api_key_id=key.id)
        assert summary.requests == 1
        assert summary.total_tokens == 150
    finally:
        session.close()
    db2.dispose()
