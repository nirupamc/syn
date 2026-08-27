"""Policy resolution for rate limits and quotas (M6).

Effective policy inheritance:
    key override ?? client value ?? system default

A missing value (None) at any level falls through to the next. A value of 0
or less means "unlimited" and short-circuits remaining levels.

This module only resolves policies; it does not enforce them. Enforcement
is the responsibility of the usage/quota service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.models.api_key import ApiKey
from app.models.client import Client


@dataclass(frozen=True)
class EffectivePolicy:
    """The resolved effective policy for a request."""

    requests_per_minute: int  # 0 = unlimited
    requests_per_day: int  # 0 = unlimited
    tokens_per_day: int  # 0 = unlimited


def _resolve(
    key_value: Optional[int],
    client_value: Optional[int],
    default_value: int,
) -> int:
    """Resolve a single policy field with the hierarchy key ?? client ?? default.

    None at any level falls through. A non-None value of 0 or less is
    "unlimited" and short-circuits to 0.
    """
    if key_value is not None:
        return max(0, key_value)
    if client_value is not None:
        return max(0, client_value)
    return max(0, default_value)


def resolve_policy(
    api_key: ApiKey,
    client: Client,
    *,
    default_requests_per_minute: int,
    default_requests_per_day: int,
    default_tokens_per_day: int,
) -> EffectivePolicy:
    """Resolve the effective policy for an API key.

    Inheritance:
        key override ?? client value ?? system default
    """
    return EffectivePolicy(
        requests_per_minute=_resolve(
            api_key.requests_per_minute,
            client.requests_per_minute,
            default_requests_per_minute,
        ),
        requests_per_day=_resolve(
            api_key.requests_per_day,
            client.requests_per_day,
            default_requests_per_day,
        ),
        tokens_per_day=_resolve(
            api_key.tokens_per_day,
            client.tokens_per_day,
            default_tokens_per_day,
        ),
    )
