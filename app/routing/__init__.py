"""Routing package public API (M9).

Exposes :func:`build_routing`, used by the application lifespan to construct the
active :class:`RoutingService`. When a routing config file is present and valid,
Syn runs in *configured* (multi-backend) mode; otherwise it runs in *passthrough*
mode, which reproduces the legacy single-backend behavior so M0-M8 usage and
tests are unaffected.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

from app.backends.registry import build_backend
from app.routing.backend_registry import BackendRegistry
from app.routing.config import (
    BackendConfig,
    ModelConfig,
    load_routing_config,
    parse_routing_config,
)
from app.routing.model_registry import ModelEntry, ModelRegistry
from app.routing.router import RoutingService


async def build_routing(
    settings,
    *,
    default_backend,
    default_backend_getter: Callable[[], object],
) -> Tuple[RoutingService, Optional[BackendRegistry]]:
    """Build the active routing service.

    Returns ``(router, backend_registry)``. ``backend_registry`` is ``None`` in
    passthrough mode.
    """
    raw = load_routing_config(settings.routing_config_path)
    if raw is None:
        router = RoutingService(
            passthrough=True,
            get_default_backend=default_backend_getter,  # type: ignore[arg-type]
        )
        return router, None

    parsed = parse_routing_config(raw)

    backend_registry = BackendRegistry(
        health_ttl_seconds=settings.backend_health_timeout_seconds
    )
    for bc in parsed.backends:
        backend = build_backend(
            bc.type,
            bc.base_url,
            timeout_seconds=settings.backend_timeout_seconds,
            connect_timeout_seconds=settings.backend_connect_timeout_seconds,
            health_timeout_seconds=settings.backend_health_timeout_seconds,
        )
        await backend.open()
        backend_registry.register(bc.id, backend)

    model_registry = ModelRegistry(
        [
            ModelEntry(
                id=m.id,
                backend_id=m.backend_id,
                backend_model=m.backend_model,
                enabled=m.enabled,
                aliases=m.aliases,
                metadata=m.metadata,
            )
            for m in parsed.models
        ]
    )

    router = RoutingService(
        model_registry=model_registry,
        backend_registry=backend_registry,
    )
    return router, backend_registry
