"""M1 isolated tests for LlamaCppBackend's real HTTP behavior.

These exercise the backend against controlled HTTP responses via an
``httpx.MockTransport``. They require NO real llama.cpp, NO GPU, and NO network.
"""

from __future__ import annotations

import httpx
import pytest

from app.backends.base import BackendHealthState
from app.backends.llama_cpp import LlamaCppBackend
from app.core.errors import (
    BackendInvalidResponseError,
    BackendProtocolError,
    BackendTimeoutError,
    BackendUnavailableError,
)


def _backend(handler, *, health_timeout=1.0):
    transport = httpx.MockTransport(handler)
    return LlamaCppBackend(
        base_url="http://127.0.0.1:8080",
        timeout_seconds=5.0,
        connect_timeout_seconds=1.0,
        health_timeout_seconds=health_timeout,
        transport=transport,
    )


# ---- health ---------------------------------------------------------------


async def test_health_healthy():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if str(request.url).endswith("/health"):
            return httpx.Response(200, json={"status": "ok"})
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "model-a", "object": "model"}]})
        return httpx.Response(404)

    backend = _backend(handler)
    await backend.open()
    try:
        result = await backend.health()
    finally:
        await backend.close()

    assert result.reachable is True
    assert result.state is BackendHealthState.REACHABLE
    assert result.reason == "healthy"
    assert any(u.endswith("/health") for u in seen)
    assert result.model == "model-a"


async def test_health_503_not_ready():
    backend = _backend(lambda req: httpx.Response(503, json={"status": "loading model"}))
    await backend.open()
    try:
        result = await backend.health()
    finally:
        await backend.close()
    assert result.reachable is False
    assert result.state is BackendHealthState.UNREACHABLE


async def test_health_unreachable_connect_error():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    backend = _backend(handler)
    await backend.open()
    try:
        result = await backend.health()
    finally:
        await backend.close()
    assert result.reachable is False
    assert result.state is BackendHealthState.UNREACHABLE
    assert "connection failed" in result.reason


async def test_health_timeout():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    backend = _backend(handler, health_timeout=0.1)
    await backend.open()
    try:
        result = await backend.health()
    finally:
        await backend.close()
    assert result.reachable is False
    assert result.state is BackendHealthState.TIMEOUT


async def test_health_invalid_json():
    backend = _backend(lambda req: httpx.Response(200, text="<html>not json</html>"))
    await backend.open()
    try:
        result = await backend.health()
    finally:
        await backend.close()
    assert result.reachable is False
    assert result.state is BackendHealthState.INVALID


async def test_health_non_ok_status_is_unreachable():
    backend = _backend(lambda req: httpx.Response(500, text="oops"))
    await backend.open()
    try:
        result = await backend.health()
    finally:
        await backend.close()
    assert result.reachable is False
    assert result.state is BackendHealthState.UNREACHABLE


async def test_health_wrong_payload_is_invalid():
    backend = _backend(lambda req: httpx.Response(200, json={"status": "weird"}))
    await backend.open()
    try:
        result = await backend.health()
    finally:
        await backend.close()
    assert result.reachable is False
    assert result.state is BackendHealthState.INVALID
# ---- model discovery ------------------------------------------------------


def _models_payload():
    return {
        "object": "list",
        "data": [
            {
                "id": "models/gemma-4-E2B-it-Q4_K_M.gguf",
                "object": "model",
                "owned_by": "llamacpp",
                "created": 1787840342,
            }
        ],
    }


async def test_models_discovery():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_models_payload())

    backend = _backend(handler)
    await backend.open()
    try:
        models = await backend.models()
    finally:
        await backend.close()

    assert seen["url"].endswith("/v1/models")
    assert len(models) == 1
    assert models[0].id == "models/gemma-4-E2B-it-Q4_K_M.gguf"
    assert models[0].owned_by == "llamacpp"


async def test_models_http_error_raises_protocol():
    backend = _backend(lambda req: httpx.Response(500, text="internal"))
    await backend.open()
    try:
        with pytest.raises(BackendProtocolError):
            await backend.models()
    finally:
        await backend.close()


async def test_models_invalid_json_raises():
    backend = _backend(lambda req: httpx.Response(200, text="not json"))
    await backend.open()
    try:
        with pytest.raises(BackendInvalidResponseError):
            await backend.models()
    finally:
        await backend.close()


async def test_models_times_out():
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    backend = _backend(handler)
    await backend.open()
    try:
        with pytest.raises(BackendTimeoutError):
            await backend.models()
    finally:
        await backend.close()


async def test_models_unreachable_raises():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    backend = _backend(handler)
    await backend.open()
    try:
        with pytest.raises(BackendUnavailableError):
            await backend.models()
    finally:
        await backend.close()


async def test_models_empty_data_returns_empty_set():
    backend = _backend(lambda req: httpx.Response(200, json={"object": "list", "data": []}))
    await backend.open()
    try:
        assert await backend.models() == []
    finally:
        await backend.close()


async def test_models_missing_data_array_is_invalid():
    backend = _backend(lambda req: httpx.Response(200, json={"object": "list"}))
    await backend.open()
    try:
        with pytest.raises(BackendInvalidResponseError):
            await backend.models()
    finally:
        await backend.close()