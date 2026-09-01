"""In-process throttling for registration and migration endpoints.

The limiter is per-process and in-memory: with multiple workers each worker
enforces its own budget, so it is a best-effort control against scripted abuse
rather than a hard global quota. Client IPs come from the ASGI scope's
``client`` value. Uvicorn resolves trusted forwarded headers before the request
reaches the application; the application deliberately does not parse them a
second time.
"""

import re
import threading
import time
from collections import deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from registry_api.client_address import normalize_client_ip
from registry_api.errors import ErrorCode

_MAX_TRACKED_CLIENTS = 10_000
_MIGRATION_PATH = re.compile(r"/v2/assets/[^/]+/migrate")


class _SlidingWindowLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = {}
        self._last_pruned = 0.0

    def allow(self, key: str, *, limit: int, window_seconds: float) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            events = self._events.get(key)
            if events is None:
                self._prune_locked(now, window_seconds)
                if len(self._events) >= _MAX_TRACKED_CLIENTS:
                    # A periodic prune may have run recently, but entries can
                    # expire before the next interval. At capacity, force one
                    # pass before failing closed for a previously unseen key.
                    self._prune_locked(now, window_seconds, force=True)
                if len(self._events) >= _MAX_TRACKED_CLIENTS:
                    return False
                self._events[key] = deque([now])
                return True
            while events and events[0] <= now - window_seconds:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            self._prune_locked(now, window_seconds)
            return True

    def _prune_locked(
        self, now: float, window_seconds: float, *, force: bool = False
    ) -> None:
        if not force and now - self._last_pruned < 60.0:
            return
        self._last_pruned = now
        cutoff = now - window_seconds
        stale = []
        for key, events in self._events.items():
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                stale.append(key)
        for key in stale:
            del self._events[key]

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._last_pruned = 0.0


_registration_limiter = _SlidingWindowLimiter()


class RegistrationRateLimitMiddleware:
    """Reject registration traffic before FastAPI reads or parses its body."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limit: int,
        window_seconds: float,
    ) -> None:
        self.app = app
        self.limit = limit
        self.window_seconds = window_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or self.limit <= 0
            or not _is_registration_request(scope)
        ):
            await self.app(scope, receive, send)
            return

        if _registration_limiter.allow(
            f"register:{_registration_scope_client_ip(scope)}",
            limit=self.limit,
            window_seconds=self.window_seconds,
        ):
            await self.app(scope, receive, send)
            return

        await JSONResponse(
            status_code=429,
            content={
                "error": ErrorCode.RATE_LIMITED,
                "message": "too many registration or migration requests; retry later",
            },
        )(scope, receive, send)


def _is_registration_request(scope: Scope) -> bool:
    if scope.get("method") != "POST":
        return False
    path = _route_relative_path(scope)
    return path in {"/", "/v2/assets"} or _MIGRATION_PATH.fullmatch(path) is not None


def _route_relative_path(scope: Scope) -> str:
    path = scope.get("path", "")
    root_path = scope.get("root_path", "").rstrip("/")
    if root_path and (path == root_path or path.startswith(f"{root_path}/")):
        return path[len(root_path) :] or "/"
    return path


def _registration_scope_client_ip(scope: Scope) -> str:
    client = scope.get("client")
    if client is None:
        return "unknown"
    host = client[0]
    return normalize_client_ip(host) or host
