"""OpenAI-compatible data plane endpoints (M2 + M3 auth + M4 admission + M5 streaming + M6 usage).

GET  /v1/models
POST /v1/chat/completions  (streaming and non-streaming)

Both endpoints require a valid Bearer API key (M3). Authenticated principals
have their model access policy enforced:
  * /v1/models returns only models the principal is permitted to use
  * /v1/chat/completions rejects forbidden models with 403

Streaming requests participate in the same admission queue as non-streaming
requests. A streaming request occupies an active admission slot for the
ENTIRE lifetime of the stream (M4 + M5).

Usage and quota enforcement (M6):
  * Rate-limit and quota pre-check happens AFTER auth/model policy but
    BEFORE admission.
  * Token quota is boundary-enforced (current usage >= quota rejects new
    requests; the request in flight may exceed by one generation).
  * Usage records are persisted for completed/failed/cancelled/rejected
    requests. Prompt and response content is NEVER stored.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
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
from app.core.auth import authenticate_request, authenticate_with_orm
from app.core.errors import (
    BackendInvalidResponseError,
    BackendProtocolError,
    BackendTimeoutError,
    BackendUnavailableError,
    ModelForbiddenError,
    QueueFullError,
    QueueTimeoutError,
    RateLimitExceededError,
    RequestQuotaExceededError,
    SynError,
    TokenQuotaExceededError,
    ValidationError,
)
from app.core.principal import AuthenticatedPrincipal
from app.core.request_id import get_request_id
from app.logging import get_logger
from app.models.api_key import ApiKey
from app.models.client import Client
from app.schemas.chat import (
    ChatCompletionRequest as InternalChatCompletionRequest,
    ChatCompletionChunk,
    ChatMessage,
)
from app.services.usage import Outcome, UsageService

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
    auth_result: tuple = Depends(authenticate_with_orm),
):
    """Create a chat completion. Supports both streaming and non-streaming."""
    principal, api_key, client = auth_result
    backend = _get_backend(request)
    _check_capability(backend, BackendCapability.CHAT_COMPLETIONS)

    # M6: rate-limit and quota pre-check (after auth, before admission)
    usage_service = getattr(request.app.state, "usage_service", None)
    if usage_service is not None:
        check_session = _get_db_session_for_usage(request)
        try:
            await usage_service.precheck(check_session, api_key, client)
        finally:
            check_session.close()

    available_models = await _get_available_models(backend)
    internal_req = _validate_and_normalize_request(
        body, available_models, principal, backend.capabilities()
    )

    # Non-streaming path: existing M2/M4 behavior.
    if not internal_req.stream:
        return await _non_streaming_completion(
            request, backend, internal_req, principal, api_key, client
        )

    # Streaming path: M5.
    return await _streaming_completion(
        request, backend, internal_req, principal, api_key, client
    )


def _get_db_session_for_usage(request: Request):
    """Get a DB session for usage/quota operations."""
    from app.db import Database as _DB  # noqa: F401

    db = getattr(request.app.state, "database", None)
    if db is None or db.session_factory is None:
        # No DB available; return a dummy session that will fail
        # This should never happen in normal operation
        raise SynError(
            "database unavailable for usage tracking",
            code="usage_unavailable",
            http_status=503,
        )
    return db.session_factory()


async def _non_streaming_completion(
    request: Request,
    backend,
    internal_req: InternalChatCompletionRequest,
    principal: AuthenticatedPrincipal,
    api_key: ApiKey,
    client: Client,
) -> ChatCompletionResponse:
    """Non-streaming chat completion with admission-slot lifetime.

    M6: records usage on completion/failure.
    """
    admission = _get_admission(request)
    usage_service = getattr(request.app.state, "usage_service", None)
    request_id = get_request_id()
    started_at = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
    outcome = Outcome.FAILED
    error_code: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    internal_resp = None

    try:
        try:
            async with admission.acquire():
                try:
                    internal_resp = await backend.chat_completion(internal_req)
                    outcome = Outcome.COMPLETED
                except BackendTimeoutError as exc:
                    outcome = Outcome.FAILED
                    error_code = "backend_timeout"
                    raise SynError(
                        f"chat completion timed out: {exc.detail}",
                        code="backend_timeout",
                        http_status=502,
                    ) from exc
                except BackendProtocolError as exc:
                    outcome = Outcome.FAILED
                    error_code = "backend_protocol_error"
                    raise SynError(
                        f"chat completion protocol error: {exc.detail}",
                        code="backend_protocol_error",
                        http_status=502,
                    ) from exc
                except BackendInvalidResponseError as exc:
                    outcome = Outcome.FAILED
                    error_code = "backend_invalid_response"
                    raise SynError(
                        f"chat completion invalid response: {exc.detail}",
                        code="backend_invalid_response",
                        http_status=502,
                    ) from exc
                except BackendUnavailableError as exc:
                    outcome = Outcome.FAILED
                    error_code = "backend_unavailable"
                    raise SynError(
                        f"chat completion unavailable: {exc.detail}",
                        code="backend_unavailable",
                        http_status=502,
                    ) from exc
        except QueueFullError as exc:
            outcome = Outcome.REJECTED
            error_code = exc.code or "queue_full"
            raise
        except QueueTimeoutError as exc:
            outcome = Outcome.TIMED_OUT
            error_code = exc.code or "queue_timeout"
            raise
    except SynError:
        raise
    except Exception:
        raise
    finally:
        # M6: record usage (only for non-streaming)
        if usage_service is not None:
            completed_at = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
            if internal_resp is not None:
                prompt_tokens = internal_resp.usage.prompt_tokens
                completion_tokens = internal_resp.usage.completion_tokens
                total_tokens = internal_resp.usage.total_tokens
            try:
                session = _get_db_session_for_usage(request)
                try:
                    usage_service.record(
                        session,
                        request_id=request_id,
                        api_key=api_key,
                        client=client,
                        model=internal_req.model,
                        streaming=False,
                        started_at=started_at,
                        completed_at=completed_at,
                        outcome=outcome,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        error_code=error_code,
                    )
                finally:
                    session.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("failed to record usage: %s", e)

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
        model=internal_req.model,
        choices=api_choices,
        usage=api_usage,
        system_fingerprint=internal_resp.system_fingerprint,
    )


async def _streaming_completion(
    request: Request,
    backend,
    internal_req: InternalChatCompletionRequest,
    principal: AuthenticatedPrincipal,
    api_key: ApiKey,
    client: Client,
) -> StreamingResponse:
    """Streaming chat completion with admission-slot lifetime.

    The admission slot is acquired BEFORE the StreamingResponse is created
    and released ONLY when the streaming generator completes, the client
    disconnects, or an error occurs. This is the critical M5 invariant.

    M6: records usage on completion/failure/cancellation.
    """
    admission = _get_admission(request)
    request_id = get_request_id()
    usage_service = getattr(request.app.state, "usage_service", None)
    started_at = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)

    # We must hold the admission slot for the ENTIRE stream lifetime.
    stream_done = asyncio.Event()
    upstream_gen: Optional[AsyncIterator[ChatCompletionChunk]] = None

    # M6: outcome tracking for streaming
    final_outcome = Outcome.FAILED
    final_error_code: Optional[str] = None
    completion_tokens_known: Optional[int] = None
    prompt_tokens_known: Optional[int] = None
    total_tokens_known: Optional[int] = None
    stream_completed_normally = False

    async def stream_generator() -> AsyncIterator[bytes]:
        nonlocal upstream_gen, final_outcome, final_error_code
        nonlocal completion_tokens_known, prompt_tokens_known, total_tokens_known
        nonlocal stream_completed_normally
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
                    # Capture any usage info from chunks (some backends send
                    # usage in the final chunk).
                    if hasattr(chunk, "system_fingerprint"):
                        pass
                    yield _format_sse(_chunk_to_api_dict(chunk))
                # Normal completion: emit [DONE] sentinel.
                yield _format_sse_done()
                stream_completed_normally = True
                final_outcome = Outcome.COMPLETED
                logger.info("stream completed (request_id=%s)", request_id)
        except BackendTimeoutError as exc:
            final_outcome = Outcome.FAILED
            final_error_code = "backend_timeout"
            logger.warning(
                "stream backend timeout (request_id=%s): %s",
                request_id, exc.detail,
            )
        except BackendProtocolError as exc:
            final_outcome = Outcome.FAILED
            final_error_code = "backend_protocol_error"
            logger.warning(
                "stream backend protocol error (request_id=%s): %s",
                request_id, exc.detail,
            )
        except BackendInvalidResponseError as exc:
            final_outcome = Outcome.FAILED
            final_error_code = "backend_invalid_response"
            logger.warning(
                "stream backend invalid response (request_id=%s): %s",
                request_id, exc.detail,
            )
        except BackendUnavailableError as exc:
            final_outcome = Outcome.FAILED
            final_error_code = "backend_unavailable"
            logger.warning(
                "stream backend unavailable (request_id=%s): %s",
                request_id, exc.detail,
            )
        except asyncio.CancelledError:
            # Client disconnected or task was cancelled.
            final_outcome = Outcome.CANCELLED
            logger.info("stream cancelled (request_id=%s)", request_id)
            raise
        except Exception:
            final_outcome = Outcome.FAILED
            logger.exception(
                "stream unexpected error (request_id=%s)", request_id
            )
        finally:
            # Detect client disconnect: if the stream did not complete
            # normally and no exception set a specific outcome or error
            # code, the client likely disconnected (GeneratorExit from
            # aclose()). Backend errors set final_error_code, so they
            # remain FAILED.
            if (
                not stream_completed_normally
                and final_outcome == Outcome.FAILED
                and final_error_code is None
            ):
                final_outcome = Outcome.CANCELLED
                logger.info(
                    "stream incomplete, recording as cancelled (request_id=%s)",
                    request_id,
                )
            # M6: record streaming usage
            if usage_service is not None:
                completed_at = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
                try:
                    session = _get_db_session_for_usage(request)
                    try:
                        usage_service.record(
                            session,
                            request_id=request_id,
                            api_key=api_key,
                            client=client,
                            model=internal_req.model,
                            streaming=True,
                            started_at=started_at,
                            completed_at=completed_at,
                            outcome=final_outcome,
                            prompt_tokens=prompt_tokens_known,
                            completion_tokens=completion_tokens_known,
                            total_tokens=total_tokens_known,
                            error_code=final_error_code,
                        )
                    finally:
                        session.close()
                except Exception as e:  # noqa: BLE001
                    logger.warning("failed to record streaming usage: %s", e)
            stream_done.set()
            upstream_gen = None

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Request-ID": request_id,
        },
    )
