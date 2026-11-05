"""Request start time shared with the audit service.

The response time of an audited event is the time elapsed between the reception
of the request and the writing of the event, so the audit log stays append-only.
"""

import time
from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

_request_started_at: ContextVar[float | None] = ContextVar("request_started_at", default=None)


def elapsed_ms() -> int | None:
    """Milliseconds since the current request was received, None outside a request."""
    started = _request_started_at.get()
    if started is None:
        return None
    return int((time.perf_counter() - started) * 1000)


class RequestTimingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _request_started_at.set(time.perf_counter())
        try:
            await self.app(scope, receive, send)
        finally:
            _request_started_at.reset(token)
