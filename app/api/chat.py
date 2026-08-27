"""OpenAI-compatible data plane endpoints (M2 + M3 auth).

GET  /v1/models
POST /v1/chat/completions

Both endpoints require a valid Bearer API key (M3). Authenticated principals
have their model access policy enforced:
  * /v1/models returns only models the principal is permitted to use
  * /v1/chat/completions rejects forbidden models with 403
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.chat_schemas import (
    ChatCompletionRequest as APIChatCompletionRequest,
    ChatCompletionResponse,
    ModelsListResponse,
    ModelInfo,
)
from app.backends.base import BackendCapability
from app.core.auth import authenticate_request
from app.core.errors import (
    BackendInvalidResponseError,
    BackendProtocolError,
    BackendTimeoutError,
    BackendUnavailableError,
    ModelForbiddenError,
    SynError,
    ValidationError,
)
from app.core.principal import AuthenticatedPrincipal
from app.core.request_id import get_request_id
from app.logging import get_logger
from app.schemas.chat import (
    ChatCompletionRequest as InternalChatCompletionRequest,
    ChatMessage,
)

logger = get_logger("syn.api.chat")

router = APIRouter(prefix="/v1", tags=["chat"])


def _get_backend(request: Request):
    """Get the configured backend from app state, or raise an OpenAI error."""
    backend = getattr(request.app.state, "backend", None)
    if backend is None:
        raise SynError(
            "inference backend not available",
            code="backend_not_wired",
            http_status=503,
        )
    return backend


def _check_capability(backend, capability: BackendCapability) -> None:
    """Verify the backend advertises a required capability."""
    if capability not in backend.capabilities():
        raise SynError(
            f"backend does not support {capability.value}",
            code="capability_not_supported",
            http_status=501,
        )


def _enforce_model_access(
    principal: AuthenticatedPrincipal, model_id: str
) -> None:
    """Raise ModelForbiddenError if the principal may not use the model."""
    if not principal.can_use_model(model_id):
        raise ModelForbiddenError(
            f"principal is not permitted to use model '{model_id}'",
            code="model_forbidden",
            http_status=403,
            param="model",
        )


@router.get("/models", response_model=ModelsListResponse)
async def list_models(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(authenticate_request),
) -> ModelsListResponse:
    """List available models from the configured backend, filtered by access."""
    backend = _get_backend(request)
    _check_capability(backend, BackendCapability.MODELS)

    try:
        models = await backend.models()
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

    data = [
        ModelInfo(
            id=m.id,
            object=m.object,
            owned_by=m.owned_by,
            created=m.created,
        )
        for m in models
        if principal.can_use_model(m.id)
    ]
    return ModelsListResponse(object="list", data=data)


def _validate_and_normalize_request(
    api_req: APIChatCompletionRequest,
    available_models: set[str],
    principal: AuthenticatedPrincipal,
) -> InternalChatCompletionRequest:
    """Validate API request and convert to internal typed request."""

    # Check model exists in available models
    if api_req.model not in available_models:
        raise SynError(
            f"model '{api_req.model}' not found",
            code="model_not_found",
            http_status=404,
            param="model",
        )

    # Enforce per-principal model access policy
    _enforce_model_access(principal, api_req.model)

    # Explicitly reject streaming (M2 is non-streaming only)
    if api_req.stream:
        raise SynError(
            "streaming is not supported in this version (M2)",
            code="stream_not_supported",
            http_status=400,
            param="stream",
        )

    # Convert to internal types
    messages = [ChatMessage(role=m.role, content=m.content) for m in api_req.messages]
    return InternalChatCompletionRequest(
        model=api_req.model,
        messages=messages,
        temperature=api_req.temperature,
        top_p=api_req.top_p,
        max_tokens=api_req.max_tokens,
        stop=api_req.stop,
        stream=api_req.stream,
    )


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    request: Request,
    body: APIChatCompletionRequest,
    principal: AuthenticatedPrincipal = Depends(authenticate_request),
) -> ChatCompletionResponse:
    """Create a non-streaming chat completion."""
    backend = _get_backend(request)
    _check_capability(backend, BackendCapability.CHAT_COMPLETIONS)

    # Get available models for validation
    try:
        available_models = {m.id for m in await backend.models()}
    except BackendTimeoutError as exc:
        raise SynError(
            f"model validation timed out: {exc.detail}",
            code="backend_timeout",
            http_status=502,
        ) from exc
    except BackendProtocolError as exc:
        raise SynError(
            f"model validation protocol error: {exc.detail}",
            code="backend_protocol_error",
            http_status=502,
        ) from exc
    except BackendInvalidResponseError as exc:
        raise SynError(
            f"model validation invalid response: {exc.detail}",
            code="backend_invalid_response",
            http_status=502,
        ) from exc
    except BackendUnavailableError as exc:
        raise SynError(
            f"model validation failed: {exc.detail}",
            code="backend_unavailable",
            http_status=502,
        ) from exc

    # Validate and normalize
    internal_req = _validate_and_normalize_request(body, available_models, principal)

    # Get the admission controller and acquire a slot
    admission = getattr(request.app.state, "admission", None)
    if admission is None:
        raise SynError(
            "admission controller not available",
            code="admission_not_wired",
            http_status=503,
        )

    # Call backend within admission-controlled slot
    try:
        async with admission.acquire():
            try:
                internal_resp = await backend.chat_completion(internal_req)
            except BackendTimeoutError as exc:
                raise SynError(
                    f"chat completion timed out: {exc.detail}",
                    code="backend_timeout",
                    http_status=502,
                ) from exc
            except BackendProtocolError as exc:
                raise SynError(
                    f"chat completion protocol error: {exc.detail}",
                    code="backend_protocol_error",
                    http_status=502,
                ) from exc
            except BackendInvalidResponseError as exc:
                raise SynError(
                    f"chat completion invalid response: {exc.detail}",
                    code="backend_invalid_response",
                    http_status=502,
                ) from exc
            except BackendUnavailableError as exc:
                raise SynError(
                    f"chat completion unavailable: {exc.detail}",
                    code="backend_unavailable",
                    http_status=502,
                ) from exc
    except SynError:
        raise
    except Exception:
        # Defensive: ensure no slot leakage on unexpected errors
        raise

    # Convert internal response to API response
    api_choices = [
        {
            "index": c.index,
            "message": {"role": c.message.role, "content": c.message.content},
            "finish_reason": c.finish_reason,
        }
        for c in internal_resp.choices
    ]
    api_usage = {
        "prompt_tokens": internal_resp.usage.prompt_tokens,
        "completion_tokens": internal_resp.usage.completion_tokens,
        "total_tokens": internal_resp.usage.total_tokens,
    }

    return ChatCompletionResponse(
        id=internal_resp.id or f"chatcmpl-{int(time.time() * 1000)}",
        object=internal_resp.object,
        created=internal_resp.created or int(time.time()),
        model=internal_resp.model,
        choices=api_choices,
        usage=api_usage,
        system_fingerprint=internal_resp.system_fingerprint,
    )
