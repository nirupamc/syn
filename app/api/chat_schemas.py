"""OpenAI-compatible API request/response schemas.

These are the public-facing Pydantic models for the /v1/* data plane endpoints.
They define the external contract that OpenAI SDK clients speak.

Internal service/backend types live in app.schemas.chat and are distinct from
these API models. The API layer translates between them.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ChatMessageRequest(BaseModel):
    """A single chat message in a request."""

    role: Literal["system", "user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def _content_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message content cannot be empty")
        return v


class ChatCompletionRequest(BaseModel):
    """Request for POST /v1/chat/completions."""

    model: str = Field(..., min_length=1)
    messages: list[ChatMessageRequest] = Field(..., min_length=1)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    stop: Optional[list[str]] = None
    stream: bool = False

    @field_validator("messages")
    @classmethod
    def _messages_not_empty(cls, v: list[ChatMessageRequest]) -> list[ChatMessageRequest]:
        if not v:
            raise ValueError("messages array cannot be empty")
        return v


class ModelInfo(BaseModel):
    """Model info for GET /v1/models response."""

    id: str
    object: Literal["model"] = "model"
    owned_by: str = "llamacpp"
    created: Optional[int] = None


class ModelsListResponse(BaseModel):
    """Response for GET /v1/models."""

    object: Literal["list"] = "list"
    data: list[ModelInfo]


class ChatMessageResponse(BaseModel):
    """A single chat message in a response."""

    role: Literal["assistant"] = "assistant"
    content: str


class ChatCompletionChoice(BaseModel):
    """A single completion choice."""

    index: int
    message: ChatMessageResponse
    finish_reason: Optional[str] = None


class ChatCompletionUsage(BaseModel):
    """Token usage information."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    """Response for POST /v1/chat/completions (non-streaming)."""

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage
    system_fingerprint: Optional[str] = None


# OpenAI-compatible error response format
class OpenAIError(BaseModel):
    """OpenAI-style error object."""

    message: str
    type: str
    param: Optional[str] = None
    code: Optional[str] = None


class OpenAIErrorResponse(BaseModel):
    """OpenAI-compatible error response wrapper."""

    error: OpenAIError