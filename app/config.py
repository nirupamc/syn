"""Typed application configuration backed by pydantic-settings.

All runtime configuration lives here so that no code in the package reads
process environment variables directly. Values may be provided through real
environment variables (prefixed with ``SYN_``) or a local ``.env`` file, and
every field has a documented, safe development default.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Runtime environment for the gateway."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class BackendType(StrEnum):
    """Identifiers for supported inference backends.

    Only ``LLAMA_CPP`` is a declared type during M0. The backend integration
    itself is implemented in M1; see ``app/backends``.
    """

    LLAMA_CPP = "llama_cpp"


class Settings(BaseSettings):
    """Typed, validated application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SYN_",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    # Application identity -------------------------------------------------
    app_name: str = "Syn"
    app_version: str = "0.1.0"
    environment: Environment = Environment.DEVELOPMENT

    # Serving --------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8001
    log_level: str = "INFO"

    # Database --------------------------------------------------------------
    database_url: str = "sqlite:///./data/syn.db"

    # Inference backend -------------------------------------------------------
    backend_type: BackendType = BackendType.LLAMA_CPP
    backend_base_url: str = "http://127.0.0.1:8080"
    backend_timeout_seconds: float = 120.0
    # Connect timeout for the outbound HTTP client (per-connection).
    backend_connect_timeout_seconds: float = 10.0
    # Overall timeout used specifically for backend health probes, so that a
    # health poll does not block for the full request timeout.
    backend_health_timeout_seconds: float = 5.0

    # Admin / management plane (M3) ------------------------------------------
    # The admin plane is protected by a separate bootstrap secret. If empty
    # (the default in production), the admin plane returns 401. In development
    # or testing, callers may set SYN_ADMIN_SECRET to enable admin operations
    # over loopback. This is NOT a full admin auth system.
    admin_secret: str = ""

    # Admission control (M4) -----------------------------------------------
    # Maximum number of chat-completion requests that may execute against
    # the backend concurrently. Beyond this, requests wait in the queue.
    max_active_requests: int = 1
    # Maximum number of chat-completion requests that may wait in the queue.
    # Queue-full requests are rejected immediately (429).
    max_queue_size: int = 8
    # Maximum time (seconds) a request may wait in the queue before being
    # rejected (503 queue_timeout).
    queue_timeout_seconds: float = 30.0

    # Usage / quotas / rate limits (M6) ------------------------------------
    # System-wide defaults. Per-client and per-key overrides may be set via
    # the management plane. None or 0 means unlimited.
    default_requests_per_minute: int = 0      # 0 = unlimited
    default_requests_per_day: int = 0        # 0 = unlimited
    default_tokens_per_day: int = 0          # 0 = unlimited

    # Remote deployment / hardening (M8) -----------------------------------
    # Maximum request body size in bytes. Requests with Content-Length exceeding
    # this are rejected with 413. Default 1 MiB (1_048_576) is safe for normal
    # chat completions (few KB) while preventing unbounded buffering.
    max_request_body_bytes: int = 1_048_576

    # CORS allowed origins as a comma-separated list. Empty (default) means
    # no CORS headers are emitted (restrictive). Example:
    #   SYN_CORS_ALLOWED_ORIGINS="https://app.example.com,https://admin.example.com"
    # Wildcard "*" is NOT allowed with credentials and is rejected; use explicit origins.
    cors_allowed_origins: str = ""

    @field_validator("port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        if not (1 <= value <= 65535):
            raise ValueError(f"port must be between 1 and 65535, got {value}")
        return value

    @field_validator("backend_timeout_seconds")
    @classmethod
    def _validate_backend_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError(f"backend_timeout_seconds must be positive, got {value}")
        return value

    @field_validator("backend_connect_timeout_seconds")
    @classmethod
    def _validate_backend_connect_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError(
                f"backend_connect_timeout_seconds must be positive, got {value}"
            )
        return value

    @field_validator("backend_health_timeout_seconds")
    @classmethod
    def _validate_backend_health_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError(
                f"backend_health_timeout_seconds must be positive, got {value}"
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid log level: {value}")
        return normalized

    @field_validator("max_active_requests")
    @classmethod
    def _validate_max_active(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(f"max_active_requests must be positive, got {value}")
        return value

    @field_validator("max_queue_size")
    @classmethod
    def _validate_max_queue(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"max_queue_size must be >= 0, got {value}")
        return value

    @field_validator("queue_timeout_seconds")
    @classmethod
    def _validate_queue_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError(
                f"queue_timeout_seconds must be positive, got {value}"
            )
        return value

    @field_validator("default_requests_per_minute")
    @classmethod
    def _validate_rpm(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"default_requests_per_minute must be >= 0, got {value}")
        return value

    @field_validator("default_requests_per_day")
    @classmethod
    def _validate_rpd(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"default_requests_per_day must be >= 0, got {value}")
        return value

    @field_validator("default_tokens_per_day")
    @classmethod
    def _validate_tpd(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"default_tokens_per_day must be >= 0, got {value}")
        return value

    @field_validator("max_request_body_bytes")
    @classmethod
    def _validate_max_body(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(f"max_request_body_bytes must be positive, got {value}")
        if value > 50 * 1024 * 1024:
            raise ValueError(f"max_request_body_bytes too large (max 50 MiB), got {value}")
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _validate_cors(cls, value: str) -> str:
        # Reject wildcard with credentials; we enforce explicit origins.
        # Empty string means restrictive (no CORS).
        if value.strip() == "*":
            raise ValueError("cors_allowed_origins cannot be '*'; use explicit origins or leave empty")
        # Basic sanity: each origin should look like http(s)://...
        if value.strip():
            for part in value.split(","):
                part = part.strip()
                if not part:
                    continue
                if part == "*":
                    raise ValueError("wildcard not allowed in cors_allowed_origins")
                if not (part.startswith("http://") or part.startswith("https://")):
                    raise ValueError(f"cors origin must start with http:// or https://, got {part!r}")
        return value

    @property
    def cors_origins_list(self) -> list[str]:
        """Parsed list of allowed CORS origins; empty means restrictive."""
        if not self.cors_allowed_origins.strip():
            return []
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, cached for the lifetime of the app."""
    return Settings()