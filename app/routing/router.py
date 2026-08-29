"""Deterministic routing service (M9).

Given a requested model and an authenticated principal, produces a
:class:`RouteDecision` that identifies:

* the canonical (public) model ID,
* the backend id,
* the backend-native model id the backend should receive,
* the resolved backend instance, and
* a human-readable reason (for observability).

Routing is deterministic and fail-fast:

* resolve canonical model / alias,
* verify the model is enabled,
* verify the principal is permitted to use the canonical model (aliases are
  NOT a way to bypass model policy),
* resolve the configured backend,
* verify the backend exists.

There is deliberately **no** automatic fallback to another backend or model.

Two operating modes:

* *configured* — driven by a ``ModelRegistry`` + ``BackendRegistry`` built from
  ``config/routing.json``.
* *passthrough* — legacy single-backend behavior (M0-M8 preserved). The
  requested model is forwarded as-is to the default backend; model existence is
  validated exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.api.chat_schemas import ModelInfo
from app.backends.base import InferenceBackend
from app.core.errors import (
    BackendInvalidResponseError,
    BackendNotConfiguredError,
    BackendProtocolError,
    BackendTimeoutError,
    BackendUnavailableError,
    ModelDisabledError,
    ModelForbiddenError,
    ModelNotFoundError,
    SynError,
)
from app.core.principal import AuthenticatedPrincipal
from app.routing.backend_registry import BackendRegistry
from app.routing.model_registry import ModelEntry, ModelRegistry


@dataclass(frozen=True)
class RouteDecision:
    """The result of a routing decision."""

    requested_model: str
    canonical_model: str
    backend_id: str
    backend_model: str
    reason: str
    backend: InferenceBackend


class RoutingService:
    """Resolves models to backends deterministically."""

    def __init__(
        self,
        *,
        model_registry: Optional[ModelRegistry] = None,
        backend_registry: Optional[BackendRegistry] = None,
        passthrough: bool = False,
        get_default_backend: Optional[Callable[[], InferenceBackend]] = None,
    ) -> None:
        self._model_registry = model_registry
        self._backend_registry = backend_registry
        self._passthrough = passthrough
        self._get_default_backend = get_default_backend

    @property
    def configured(self) -> bool:
        """True when driven by an explicit routing config (multi-backend)."""
        return not self._passthrough

    @property
    def backend_registry(self) -> Optional[BackendRegistry]:
        return self._backend_registry

    # ---- model listing ------------------------------------------------------

    async def list_models(self, principal: AuthenticatedPrincipal) -> list[ModelInfo]:
        """Return the models visible to the principal."""
        if self._passthrough:
            backend = self._require_default_backend()
            try:
                discovered = await backend.models()
            except BackendTimeoutError as exc:
                raise SynError(
                    f"model discovery timed out: {exc.detail}",
                    code="backend_timeout",
                    http_status=502,
                ) from exc
            except BackendProtocolError as exc:
                raise SynError(
                    f"model discovery protocol error: {exc.detail}",
                    code="backend_protocol_error",
                    http_status=502,
                ) from exc
            except BackendInvalidResponseError as exc:
                raise SynError(
                    f"model discovery invalid response: {exc.detail}",
                    code="backend_invalid_response",
                    http_status=502,
                ) from exc
            except BackendUnavailableError as exc:
                raise SynError(
                    f"model discovery failed: {exc.detail}",
                    code="backend_unavailable",
                    http_status=502,
                ) from exc
            return [
                ModelInfo(
                    id=m.id,
                    object=m.object,
                    owned_by=m.owned_by,
                    created=m.created,
                )
                for m in discovered
                if principal.can_use_model(m.id)
            ]

        assert self._model_registry is not None
        out: list[ModelInfo] = []
        for entry in self._model_registry.list_enabled():
            if not principal.can_use_model(entry.id):
                continue
            out.append(
                ModelInfo(
                    id=entry.id,
                    object="model",
                    owned_by="syn",
                    created=None,
                )
            )
        return out

    # ---- routing ------------------------------------------------------------

    async def route(
        self, requested_model: str, principal: AuthenticatedPrincipal
    ) -> RouteDecision:
        """Resolve a requested model to a backend.

        Raises:
            ModelNotFoundError       — unknown model/alias (404)
            ModelDisabledError       — model exists but disabled (404)
            ModelForbiddenError      — principal not permitted (403)
            BackendNotConfiguredError — referenced backend missing (502)
        """
        if self._passthrough:
            backend = self._require_default_backend()
            try:
                discovered = await backend.models()
            except Exception:
                discovered = []
            available = {m.id for m in discovered}
            if requested_model not in available:
                raise ModelNotFoundError(
                    f"model '{requested_model}' not found",
                    code="model_not_found",
                )
            if not principal.can_use_model(requested_model):
                raise ModelForbiddenError(
                    f"principal is not permitted to use model '{requested_model}'",
                    code="model_forbidden",
                    http_status=403,
                    param="model",
                )
            return RouteDecision(
                requested_model=requested_model,
                canonical_model=requested_model,
                backend_id="default",
                backend_model=requested_model,
                reason="passthrough",
                backend=backend,
            )

        assert self._model_registry is not None
        assert self._backend_registry is not None
        entry: ModelEntry = self._model_registry.resolve(requested_model)
        if not entry.enabled:
            raise ModelDisabledError(
                f"model '{entry.id}' is disabled",
                code="model_disabled",
            )
        if not principal.can_use_model(entry.id):
            raise ModelForbiddenError(
                f"principal is not permitted to use model '{entry.id}'",
                code="model_forbidden",
                http_status=403,
                param="model",
            )
        backend = self._backend_registry.get(entry.backend_id)
        reason = "alias_match" if requested_model != entry.id else "canonical_match"
        return RouteDecision(
            requested_model=requested_model,
            canonical_model=entry.id,
            backend_id=entry.backend_id,
            backend_model=entry.backend_model,
            reason=reason,
            backend=backend,
        )

    def _require_default_backend(self) -> InferenceBackend:
        if self._get_default_backend is None:
            raise BackendNotConfiguredError(
                "no default backend configured (passthrough mode unavailable)",
                code="backend_not_configured",
            )
        backend = self._get_default_backend()
        if backend is None:
            raise BackendNotConfiguredError(
                "default backend is not available",
                code="backend_not_configured",
            )
        return backend
