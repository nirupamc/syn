"""Internal chat completion request/response types.

These are Syn-owned typed structures used at the service/backend boundary.
They are NOT the OpenAI API models (those live in app/api/chat_schemas.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ChatMessage:
    """A single chat message in the conversation."""

    role: str
    content: str


@dataclass(frozen=True)
class ChatCompletionRequest:
    """Normalized, validated request for a single chat completion."""

    model: str
    messages: list[ChatMessage]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stop: Optional[list[str]] = None
    stream: bool = False


@dataclass(frozen=True)
class ChatCompletionChoice:
    """A single completion choice."""

    index: int
    message: ChatMessage
    finish_reason: Optional[str] = None


@dataclass(frozen=True)
class ChatCompletionUsage:
    """Token usage information."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class ChatCompletionResponse:
    """Normalized response from a chat completion."""

    id: str
    object: str
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage
    system_fingerprint: Optional[str] = None