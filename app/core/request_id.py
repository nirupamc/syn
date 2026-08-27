"""Request / correlation ID foundation.

Every inbound request is assigned a unique request ID that is propagated
through the gateway, queue, backend, usage accounting, and logs. In M0 this is
provided by ASGI middleware; later milestones may extend it to outbound
backend calls and persistence.

We never log request bodies, prompts, or Authorization values.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Header used to accept an upstream correlation ID and to echo it back.
REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def get_request_id() -> Optional[str]:
    """Return the request ID bound to the current async context, if any."""
    return _request_id.get()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign and propagate a request/correlation ID for each request."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = _sanitize(incoming) or str(uuid.uuid4())

        token = _request_id.set(request_id)
        try:
            response = await call_next(request)
        except Exception:
            # Do not swallow the exception: the global handler will deal.
            _request_id.reset(token)
            raise
        response.headers[REQUEST_ID_HEADER] = request_id
        _request_id.reset(token)
        return response


def _sanitize(value: Optional[str]) -> Optional[str]:
    """Return a safe request ID or None.

    Accepts only a bounded, printable, single-line value to avoid header
    injection / extremely long values.
    """
    if not value:
        return None
    value = value.strip()
    if not value or len(value) > 64 or any(char.isspace() for char in value):
        return None
    return value