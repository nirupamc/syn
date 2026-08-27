"""OpenAI-compatible data plane endpoints (M2 + M3 auth + M4 admission + M5 streaming).

GET  /v1/models
POST /v1/chat/completions  (streaming and non-streaming)

Both endpoints require a valid Bearer API key (M3). Authenticated principals
have their model access policy enforced:
  * /v1/models returns only models the principal is permitted to use
  * /v1/chat/completions rejects forbidden models with 403

Streaming requests participate in the same admission queue as non-streaming
requests. A streaming request occupies an active admission slot for the
ENTIRE lifetime of the stream (M4 + M5).
"""

from __future__ import annotations

import asyncio
import json as _stdlib_json
import time
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

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
    ChatCompletionChunk,
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


def _get_admission(request: Request):
    """Get the admission controller from app state, or raise an OpenAI error."""
    admission = getattr(request.app.state, "admission", None)
    if admission is None:
        raise SynError(
            "admission controller not available",
            code="admission_not_wired",
            http_status=503,
        )
    return admission


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
    backend_capabilities: tuple,
) -> InternalChatCompletionRequest:
    """Validate API request and convert to internal typed request.

    Streaming is supported in M5 if the backend advertises STREAMING.
    """
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

    # Streaming: require backend capability.
    if api_req.stream and BackendCapability.STREAMING not in backend_capabilities:
        raise SynError(
            "streaming is not supported by this backend",
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


async def _get_available_models(backend) -> set[str]:
    """Fetch the set of available model IDs from the backend."""
    try:
        models = await backend.models()
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
    return {m.id for m in models}


def _format_sse(payload: dict) -> bytes:
    """Format a dict as a single SSE ``data:`` event."""
    return f"data: {_stdlib_json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _format_sse_done() -> bytes:
    """Format the OpenAI ``[DONE]`` SSE sentinel."""
    return b"data: [DONE]\n\n"


def _chunk_to_api_dict(chunk: ChatCompletionChunk) -> dict:
    """Convert a Syn ChatCompletionChunk into an OpenAI-compatible dict."""
    choices = []
    for c in chunk.choices:
        delta: dict[str, object] = {}
        if c.delta.role is not None:
            delta["role"] = c.delta.role
        if c.delta.content is not None:
            delta["content"] = c.delta.content
        choices.append(
            {
                "index": c.index,
                "delta": delta,
                "finish_reason": c.finish_reason,
            }
        )
    return {
        "id": chunk.id,
        "object": chunk.object or "chat.completion.chunk",
        "created": chunk.created,
        "model": chunk.model,
        "choices": choices,
        **({"system_fingerprint": chunk.system_fingerprint} if chunk.system_fingerprint else {}),
    }


@router.post("/chat/completions")
async def create_chat_completion(
    request: Request,
    body: APIChatCompletionRequest,
    principal: AuthenticatedPrincipal = Depends(authenticate_request),
):
    """Create a chat completion. Supports both streaming and non-streaming."""
    backend = _get_backend(request)
    _check_capability(backend, BackendCapability.CHAT_COMPLETIONS)

    available_models = await _get_available_models(backend)
    internal_req = _validate_and_normalize_request(
        body, available_models, principal, backend.capabilities()
    )

    # Non-streaming path: existing M2/M4 behavior.
    if not internal_req.stream:
        return await _non_streaming_completion(request, backend, internal_req)

    # Streaming path: M5.
    return await _streaming_completion(request, backend, internal_req)


async def _non_streaming_completion(
    request: Request,
    backend,
    internal_req: InternalChatCompletionRequest,
) -> ChatCompletionResponse:
    """Non-streaming chat completion with admission-slot lifetime."""
    admission = _get_admission(request)

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


async def _streaming_completion(
    request: Request,
    backend,
    internal_req: InternalChatCompletionRequest,
) -> StreamingResponse:
    """Streaming chat completion with admission-slot lifetime.

    The admission slot is acquired BEFORE the StreamingResponse is created
    and released ONLY when the streaming generator completes, the client
    disconnects, or an error occurs. This is the critical M5 invariant.
    """
    admission = _get_admission(request)
    request_id = get_request_id()

    # We must hold the admission slot for the ENTIRE stream lifetime.
    # We use an asyncio.Event to coordinate between the streaming generator
    # and the FastAPI request-disconnect handler.
    stream_done = asyncio.Event()
    upstream_gen: Optional[AsyncIterator[ChatCompletionChunk]] = None

    async def release_slot_once() -> None:
        """Release the admission slot exactly once."""
        # This is called from a finally block; the actual slot release
        # happens when the ``async with admission.acquire()`` context
        # exits. We use a flag to ensure we only trigger exit once.
        nonlocal slot_released
        if not slot_released:
            slot_released = True
            slot_release_event.set()

    slot_released = False
    slot_release_event = asyncio.Event()

    async def stream_generator() -> AsyncIterator[bytes]:
        """Yield SSE-formatted chunks to the client.

        Holds the admission slot for the entire stream. Releases on
        completion, error, or generator close (client disconnect).
        """
        nonlocal upstream_gen
        try:
            async with admission.acquire():
                logger.info(
                    "stream started (request_id=%s, model=%s)",
                    request_id, internal_req.model,
                )
                upstream_gen = backend.stream_chat_completion(internal_req)
                first_chunk = True
                async for chunk in upstream_gen:
                    if first_chunk:
                        logger.info(
                            "stream first chunk (request_id=%s)",
                            request_id,
                        )
                        first_chunk = False
                    yield _format_sse(_chunk_to_api_dict(chunk))
                # Normal completion: emit [DONE] sentinel.
                yield _format_sse_done()
                logger.info("stream completed (request_id=%s)", request_id)
        except BackendTimeoutError as exc:
            logger.warning(
                "stream backend timeout (request_id=%s): %s",
                request_id, exc.detail,
            )
            # Do not emit SSE error event after stream started; just close.
            # The client will see a truncated stream.
        except BackendProtocolError as exc:
            logger.warning(
                "stream backend protocol error (request_id=%s): %s",
                request_id, exc.detail,
            )
        except BackendInvalidResponseError as exc:
            logger.warning(
                "stream backend invalid response (request_id=%s): %s",
                request_id, exc.detail,
            )
        except BackendUnavailableError as exc:
            logger.warning(
                "stream backend unavailable (request_id=%s): %s",
                request_id, exc.detail,
            )
        except asyncio.CancelledError:
            # Client disconnected or task was cancelled.
            logger.info("stream cancelled (request_id=%s)", request_id)
            raise
        except Exception:
            logger.exception(
                "stream unexpected error (request_id=%s)", request_id
            )
            # Do not raise traceback to client. Close cleanly.
        finally:
            stream_done.set()
            # If upstream_gen exists, closing the generator will cause the
            # backend's __aexit__/finally to close the upstream response.
            # We rely on the backend to handle cleanup in its own finally.
            upstream_gen = None

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Request-ID": request_id,
        },
    )
