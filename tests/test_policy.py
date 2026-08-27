"""M6 tests: policy resolution."""

from __future__ import annotations

from app.core.policy import resolve_policy


def _make_key(requests_per_minute=None, requests_per_day=None, tokens_per_day=None):
    """Create a minimal mock ApiKey-like object."""
    return type("K", (), {
        "requests_per_minute": requests_per_minute,
        "requests_per_day": requests_per_day,
        "tokens_per_day": tokens_per_day,
    })()


def _make_client(requests_per_minute=None, requests_per_day=None, tokens_per_day=None):
    """Create a minimal mock Client-like object."""
    return type("C", (), {
        "requests_per_minute": requests_per_minute,
        "requests_per_day": requests_per_day,
        "tokens_per_day": tokens_per_day,
    })()


def test_all_defaults_when_nothing_set():
    key = _make_key()
    client = _make_client()
    policy = resolve_policy(
        key, client,
        default_requests_per_minute=10,
        default_requests_per_day=1000,
        default_tokens_per_day=50000,
    )
    assert policy.requests_per_minute == 10
    assert policy.requests_per_day == 1000
    assert policy.tokens_per_day == 50000


def test_client_overrides_default():
    key = _make_key()
    client = _make_client(requests_per_minute=5)
    policy = resolve_policy(
        key, client,
        default_requests_per_minute=10,
        default_requests_per_day=1000,
        default_tokens_per_day=50000,
    )
    assert policy.requests_per_minute == 5
    assert policy.requests_per_day == 1000  # still default


def test_key_overrides_client():
    key = _make_key(requests_per_minute=2)
    client = _make_client(requests_per_minute=5)
    policy = resolve_policy(
        key, client,
        default_requests_per_minute=10,
        default_requests_per_day=1000,
        default_tokens_per_day=50000,
    )
    assert policy.requests_per_minute == 2


def test_unlimited_key_overrides_client():
    """A value of 0 (unlimited) at the key level should not be overridden by client."""
    key = _make_key(requests_per_minute=0)
    client = _make_client(requests_per_minute=5)
    policy = resolve_policy(
        key, client,
        default_requests_per_minute=10,
        default_requests_per_day=1000,
        default_tokens_per_day=50000,
    )
    assert policy.requests_per_minute == 0  # 0 means unlimited


def test_unlimited_default():
    """Default of 0 means unlimited."""
    key = _make_key()
    client = _make_client()
    policy = resolve_policy(
        key, client,
        default_requests_per_minute=0,
        default_requests_per_day=0,
        default_tokens_per_day=0,
    )
    assert policy.requests_per_minute == 0
    assert policy.requests_per_day == 0
    assert policy.tokens_per_day == 0


def test_negative_values_treated_as_zero():
    """Negative values should be normalized to 0 (unlimited)."""
    key = _make_key(requests_per_minute=-5)
    client = _make_client()
    policy = resolve_policy(
        key, client,
        default_requests_per_minute=10,
        default_requests_per_day=1000,
        default_tokens_per_day=50000,
    )
    assert policy.requests_per_minute == 0


def test_mixed_hierarchy():
    """Each field can have different override sources."""
    key = _make_key(requests_per_minute=2)  # key override
    client = _make_client(
        requests_per_minute=5,
        requests_per_day=500,  # client override
    )
    policy = resolve_policy(
        key, client,
        default_requests_per_minute=10,
        default_requests_per_day=1000,
        default_tokens_per_day=50000,
    )
    assert policy.requests_per_minute == 2  # from key
    assert policy.requests_per_day == 500  # from client
    assert policy.tokens_per_day == 50000  # from default
