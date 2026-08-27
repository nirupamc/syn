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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, cached for the lifetime of the app."""
    return Settings()