"""Consistent internal/API error model.

These errors are the single source of truth for how Syn signal failures both
internally and (eventually, in later milestones) to OpenAI-compatible clients.

During M0 we keep the taxonomy deliberately small and intentional. Later
milestones will refine the mapping to OpenAI-style error responses without
reproducing the entire OpenAI error taxonomy.

Every Syn error carries:

* a stable ``code`` string used as the machine-readable identifier,
* a human-readable ``detail`` message,
* an optional ``http_status`` for HTTP translation, and
* an optional ``request_id`` used for tracing.
"""

from __future__ import annotations

from typing import Optional


class SynError(Exception):
    """Base class for all Syn errors."""

    code: str = "syn_error"
    default_message: str = "An internal error occurred."
    http_status: int = 500

    def __init__(
        self,
        detail: Optional[str] = None,
        *,
        code: Optional[str] = None,
        http_status: Optional[int] = None,
        request_id: Optional[str] = None,
        param: Optional[str] = None,
    ) -> None:
        self.detail = detail if detail is not None else self.__class__.default_message
        self.code = code or self.__class__.code
        self.http_status = http_status or self.__class__.http_status
        self.request_id = request_id
        self.param = param
        super().__init__(self.detail)

    def to_dict(self) -> dict[str, object]:
        """Serialize to the internal API error representation."""
        payload: dict[str, object] = {
            "code": self.code,
            "detail": self.detail,
        }
        if self.param:
            payload["param"] = self.param
        if self.request_id:
            payload["request_id"] = self.request_id
        return payload


class ConfigurationError(SynError):
    """Raised when application configuration is invalid or inconsistent."""

    code = "configuration_error"
    http_status = 500


class ValidationError(SynError):
    """Request payload / admission validation failed."""

    code = "validation_error"
    http_status = 400


class NotFoundError(SynError):
    """The requested resource does not exist."""

    code = "not_found"
    http_status = 404


class AuthenticationError(SynError):
    """Authentication failed (M3)."""

    code = "authentication_required"
    http_status = 401


class InvalidApiKeyError(AuthenticationError):
    """The presented API key is invalid (not found or wrong)."""

    code = "invalid_api_key"
    http_status = 401


class RevokedApiKeyError(AuthenticationError):
    """The presented API key has been revoked."""

    code = "invalid_api_key"
    http_status = 401


class ExpiredApiKeyError(AuthenticationError):
    """The presented API key has expired."""

    code = "expired_api_key"
    http_status = 401


class ModelForbiddenError(SynError):
    """The authenticated principal is not permitted to use the requested model."""

    code = "model_forbidden"
    http_status = 403


class AdminAuthError(SynError):
    """Management-plane operation requires admin authentication (M3)."""

    code = "admin_auth_required"
    http_status = 401


class PermissionError_(SynError):
    """Authorization failed (future; M3)."""

    code = "permission_denied"
    http_status = 403


class BackendUnavailableError(SynError):
    """The configured inference backend is not reachable/healthy."""

    code = "backend_unavailable"
    http_status = 502


class BackendTimeoutError(BackendUnavailableError):
    """The backend did not respond within the configured timeout."""

    code = "backend_timeout"
    http_status = 504


class BackendInvalidResponseError(BackendUnavailableError):
    """The backend returned a malformed / unparseable response body."""

    code = "backend_invalid_response"
    http_status = 502


class BackendProtocolError(BackendUnavailableError):
    """The backend returned an error HTTP status (non-2xx)."""

    code = "backend_protocol_error"
    http_status = 502


class QueueFullError(SynError):
    """The admission queue is at capacity; request was rejected immediately."""

    code = "queue_full"
    http_status = 429


class QueueTimeoutError(SynError):
    """The request waited in the queue longer than the configured timeout."""

    code = "queue_timeout"
    http_status = 503


class RateLimitExceededError(SynError):
    """The request exceeded the configured request-rate limit."""

    code = "rate_limit_exceeded"
    http_status = 429


class RequestQuotaExceededError(SynError):
    """The request would exceed the configured daily request quota."""

    code = "request_quota_exceeded"
    http_status = 429


class TokenQuotaExceededError(SynError):
    """The request would exceed the configured daily token quota."""

    code = "token_quota_exceeded"
    http_status = 429


class RequestBodyTooLargeError(SynError):
    """The request body exceeds the configured maximum size."""

    code = "request_body_too_large"
    http_status = 413


class ModelNotFoundError(SynError):
    """The requested model (or alias) is not registered in the model registry."""

    code = "model_not_found"
    http_status = 404


class ModelDisabledError(SynError):
    """The requested model exists but is disabled in the model registry."""

    code = "model_disabled"
    http_status = 404


class BackendNotConfiguredError(SynError):
    """The backend referenced by a model/route is not configured."""

    code = "backend_not_configured"
    http_status = 502


class RoutingConfigError(ConfigurationError):
    """The routing configuration (backends/models) is invalid."""

    code = "routing_config_invalid"
    http_status = 500