"""Admission controls for the single-worker hosted demo."""

from datetime import UTC, datetime, timedelta
from math import ceil
from time import monotonic

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from soap_rest_ai.api.models import ErrorResponse, RestError

_MAX_BODY_BYTES = 32 * 1024
_REQUESTS_PER_MINUTE = 6
_MAX_IN_FLIGHT = 2
_LOOPBACK = {"127.0.0.1", "::1"}


def _rejection(
    status: int, code: str, message: str, retry_after: int | None = None,
) -> JSONResponse:
    error = RestError(code=code, message=message, retryable=retry_after is not None)
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(ErrorResponse(error=error).model_dump(), status_code=status, headers=headers)


class DemoAskLimits:
    def __init__(self, daily_limit: int) -> None:
        self._daily_limit = daily_limit
        self._day = None
        self._daily_requests = 0
        self._minute = -1
        self._caller_requests: dict[str, int] = {}
        self._in_flight = 0

    def _admit(self, caller: str) -> JSONResponse | None:
        # State changes run without awaiting on the demo worker's event loop.
        seconds = monotonic()
        minute = int(seconds // 60)
        if minute != self._minute:
            self._minute = minute
            self._caller_requests.clear()
        caller_requests = self._caller_requests.get(caller, 0)
        if caller_requests >= _REQUESTS_PER_MINUTE:
            return _rejection(429, "demo_rate_limit", "Demo request rate exceeded.", ceil(60 - seconds % 60))

        now = datetime.now(UTC)
        if now.date() != self._day:
            self._day = now.date()
            self._daily_requests = 0
        if self._daily_requests >= self._daily_limit:
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            return _rejection(
                429, "demo_daily_limit", "The demo has reached its daily request budget.",
                ceil((midnight - now).total_seconds()),
            )
        if self._in_flight >= _MAX_IN_FLIGHT:
            return _rejection(429, "demo_busy", "The demo is processing too many requests.", 1)

        self._caller_requests[caller] = caller_requests + 1
        self._daily_requests += 1
        self._in_flight += 1
        return None

    async def run(self, app: ASGIApp, scope: Scope, receive: Receive, send: Send) -> None:
        users = Headers(scope=scope).getlist("x-demo-user")
        if len(users) != 1 or not users[0].strip():
            response = _rejection(403, "demo_proxy_required", "Use the authenticated demo URL.")
            await response(scope, receive, send)
            return

        rejection = self._admit(users[0])
        if rejection is not None:
            await rejection(scope, receive, send)
            return

        try:
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > _MAX_BODY_BYTES:
                    response = _rejection(413, "demo_body_too_large", "Demo request must fit in 32 KiB.")
                    await response(scope, receive, send)
                    return
                body.extend(chunk)
                if not message.get("more_body", False):
                    break

            pending = True

            async def receive_buffered() -> Message:
                nonlocal pending
                if pending:
                    pending = False
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await app(scope, receive_buffered, send)
        finally:
            self._in_flight -= 1


class DemoSecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limits = getattr(scope["app"].state, "demo_limits", None) if scope["type"] == "http" else None
        if limits is not None:
            client = scope.get("client")
            if not client or client[0] not in _LOOPBACK:
                response = _rejection(403, "demo_proxy_required", "Use the authenticated demo URL.")
                await response(scope, receive, send)
                return
            if Headers(scope=scope).get("x-demo-user"):
                scope = {**scope, "scheme": "https"}
            if scope["method"] == "POST" and scope["path"] in {"/ask", "/ask/"}:
                await limits.run(self.app, scope, receive, send)
                return
        await self.app(scope, receive, send)
