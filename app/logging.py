"""Application logging configuration.

Logging policy for Syn (M0):

* Useful startup / shutdown and lifecycle logging.
* Request/correlation IDs are attached where appropriate.
* Never log secrets, Authorization values, model prompts, or user content.
* The full observability stack is future work (M7).

The formatter, when running inside a request, includes the current request ID
via :func:`app.core.request_id.get_request_id`.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import Settings
from app.core.request_id import get_request_id


class RequestIDFilter(logging.Filter):
    """Attach the current request ID (if any) to a log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id: Optional[str] = get_request_id()
        record.request_id = request_id or "-"
        return True


def setup_logging(settings: Settings) -> None:
    """Configure the root logger for the application.

    Safe to call multiple times; subsequent calls are idempotent with respect
    to handler duplication.
    """
    root = logging.getLogger()
    level = logging.getLevelName(settings.log_level)
    root.setLevel(level)

    # Avoid stacking duplicate handlers on repeated calls (e.g. tests).
    for handler in list(root.handlers):
        if getattr(handler, "_syn_configured", False):
            continue

    console = logging.StreamHandler()
    console.setLevel(level)
    console._syn_configured = True  # type: ignore[attr-defined]
    console.addFilter(RequestIDFilter())
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] "
            "req=%(request_id)s %(message)s"
        )
    )
    root.addHandler(console)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger for the caller module."""
    return logging.getLogger(name)