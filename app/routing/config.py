"""Routing configuration model and validation (M9).

Loads and validates the ``config/routing.json`` file that describes the
configured inference backends and the public model registry. Validation is
explicit and raises :class:`RoutingConfigError` with stable codes so startup
fails clearly on misconfiguration.

Matching semantics (documented for operators):

* Model IDs are **case-sensitive** and unique within the registry.
* Aliases are **case-sensitive** and must be globally unique: an alias may not
  collide with another model's canonical ID, nor with any other alias.
* No fuzzy matching is performed.

No automatic fallback, scheduling, or GPU orchestration happens here. This
module only describes *what* is configured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from app.config import BackendType
from app.core.errors import RoutingConfigError


@dataclass(frozen=True)
class BackendConfig:
    """A configured inference backend instance."""

    id: str
    type: BackendType
    base_url: str


@dataclass(frozen=True)
class ModelConfig:
    """A public model entry in the model registry."""

    id: str
    backend_id: str
    backend_model: str
    enabled: bool = True
    aliases: tuple[str, ...] = field(default=())
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedRoutingConfig:
    """Validated routing configuration."""

    backends: list[BackendConfig]
    models: list[ModelConfig]


_SUPPORTED_BACKEND_TYPES = {
    BackendType.LLAMA_CPP: BackendType.LLAMA_CPP,
}


def load_routing_config(path: str) -> Optional[dict]:
    """Load a routing config file as a dict.

    Returns ``None`` if the file does not exist or is empty. Any other read or
    JSON error is surfaced as :class:`RoutingConfigError`.
    """
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RoutingConfigError(
            f"routing config is not valid JSON: {exc}",
            code="routing_config_invalid",
        ) from exc
    if not isinstance(data, dict):
        raise RoutingConfigError(
            "routing config must be a JSON object",
            code="routing_config_invalid",
        )
    return data


def parse_routing_config(raw: dict) -> ParsedRoutingConfig:
    """Validate raw config dict into typed structures.

    Raises :class:`RoutingConfigError` (code ``routing_config_invalid``) on any
    of: empty config, duplicate backend id, unsupported backend type, duplicate
    model id, empty model id, duplicate alias, alias colliding with a canonical
    id, or a model referencing an unknown backend.
    """
    backends_raw = raw.get("backends")
    models_raw = raw.get("models")

    if not isinstance(backends_raw, list) or not backends_raw:
        raise RoutingConfigError(
            "routing config must define a non-empty 'backends' list",
            code="routing_config_invalid",
        )
    if not isinstance(models_raw, list) or not models_raw:
        raise RoutingConfigError(
            "routing config must define a non-empty 'models' list",
            code="routing_config_invalid",
        )

    backends: list[BackendConfig] = []
    seen_backend_ids: set[str] = set()
    for i, b in enumerate(backends_raw):
        if not isinstance(b, dict):
            raise RoutingConfigError(
                f"backends[{i}] must be an object",
                code="routing_config_invalid",
            )
        bid = b.get("id")
        if not isinstance(bid, str) or not bid:
            raise RoutingConfigError(
                f"backends[{i}] requires a non-empty 'id'",
                code="routing_config_invalid",
            )
        if bid in seen_backend_ids:
            raise RoutingConfigError(
                f"duplicate backend id: {bid!r}",
                code="routing_config_invalid",
            )
        btype = b.get("type", "llama_cpp")
        if not isinstance(btype, str) or btype not in _SUPPORTED_BACKEND_TYPES:
            raise RoutingConfigError(
                f"unsupported backend type: {btype!r} (supported: "
                f"{sorted(t.value for t in _SUPPORTED_BACKEND_TYPES)})",
                code="routing_config_invalid",
            )
        base_url = b.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            raise RoutingConfigError(
                f"backend {bid!r} requires a non-empty 'base_url'",
                code="routing_config_invalid",
            )
        backends.append(
            BackendConfig(
                id=bid,
                type=_SUPPORTED_BACKEND_TYPES[btype],
                base_url=base_url,
            )
        )
        seen_backend_ids.add(bid)

    models: list[ModelConfig] = []
    seen_model_ids: set[str] = set()
    seen_aliases: set[str] = set()
    for i, m in enumerate(models_raw):
        if not isinstance(m, dict):
            raise RoutingConfigError(
                f"models[{i}] must be an object",
                code="routing_config_invalid",
            )
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            raise RoutingConfigError(
                f"models[{i}] requires a non-empty 'id'",
                code="routing_config_invalid",
            )
        if mid in seen_model_ids:
            raise RoutingConfigError(
                f"duplicate model id: {mid!r}",
                code="routing_config_invalid",
            )
        backend_id = m.get("backend_id")
        if not isinstance(backend_id, str) or not backend_id:
            raise RoutingConfigError(
                f"model {mid!r} requires a non-empty 'backend_id'",
                code="routing_config_invalid",
            )
        if backend_id not in seen_backend_ids:
            raise RoutingConfigError(
                f"model {mid!r} references unknown backend: {backend_id!r}",
                code="routing_config_invalid",
            )
        backend_model = m.get("backend_model") or mid
        if not isinstance(backend_model, str) or not backend_model:
            raise RoutingConfigError(
                f"model {mid!r} requires a non-empty 'backend_model'",
                code="routing_config_invalid",
            )
        enabled = bool(m.get("enabled", True))
        raw_aliases = m.get("aliases") or []
        if not isinstance(raw_aliases, list):
            raise RoutingConfigError(
                f"model {mid!r} 'aliases' must be a list",
                code="routing_config_invalid",
            )
        aliases: list[str] = []
        for a in raw_aliases:
            if not isinstance(a, str) or not a:
                raise RoutingConfigError(
                    f"model {mid!r} has an invalid alias",
                    code="routing_config_invalid",
                )
            if a in seen_model_ids:
                raise RoutingConfigError(
                    f"alias {a!r} collides with model id {a!r}",
                    code="routing_config_invalid",
                )
            if a in seen_aliases:
                raise RoutingConfigError(
                    f"duplicate alias: {a!r}",
                    code="routing_config_invalid",
                )
            aliases.append(a)
            seen_aliases.add(a)
        models.append(
            ModelConfig(
                id=mid,
                backend_id=backend_id,
                backend_model=backend_model,
                enabled=enabled,
                aliases=tuple(aliases),
                metadata=dict(m.get("metadata") or {}),
            )
        )
        seen_model_ids.add(mid)

    return ParsedRoutingConfig(backends=backends, models=models)
