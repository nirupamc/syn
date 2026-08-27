"""llama.cpp backend placeholder (M0).

This file proves the architecture seam only. It does NOT speak to any real
llama.cpp ``llama-server``. Real connectivity (`/health`, `/v1/models`,
chat completions, streaming, cancellation) is implemented in M1/M2/M5.

Keeping this here in M0 means M1 can flesh out this class without redesigning
the application or touching API/service layers.
"""

from __future__ import annotations

from app.backends.base import BackendInfo, InferenceBackend
from app.config import BackendType


class LlamaCppBackend(InferenceBackend):
    """Placeholder for the llama.cpp ``llama-server`` backend."""

    def __init__(self, *, base_url: str = "http://127.0.0.1:8080", timeout_seconds: float = 120.0, **_: object) -> None:
        super().__init__(base_url=base_url, timeout_seconds=timeout_seconds)

    @property
    def name(self) -> str:
        return "llama_cpp"

    @property
    def backend_info(self) -> BackendInfo:
        return BackendInfo(
            name=self.name,
            display_name="llama.cpp",
            base_url=self.base_url,
            capabilities=(),  # not claimed until M1
            timeout_seconds=self.timeout_seconds,
        )


# Register eagerly so the registry resolves llama_cpp from configuration.
from app.backends.registry import register  # noqa: E402

register(BackendType.LLAMA_CPP, LlamaCppBackend)  # noqa: F821