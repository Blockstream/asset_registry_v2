"""In-process throttling for registration and migration endpoints.

The limiter is per-process and in-memory: with multiple workers each worker
enforces its own budget, so it is a best-effort control against scripted abuse
rather than a hard global quota. Client IPs come from the ASGI server's
``request.client`` value. Uvicorn resolves trusted forwarded headers before the
request reaches the application; the application deliberately does not parse
them a second time.
"""

from collections import deque
import threading
import time

from fastapi import Depends, Request

from registry_api.client_address import normalize_client_ip
from registry_api.errors import ErrorCode, RegistryError
from registry_api.settings import Settings, get_settings

_MAX_TRACKED_CLIENTS = 10_000


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


def _registration_client_ip(request: Request) -> str:
    """Return the client identity already resolved by the ASGI server.

    The Docker entrypoint enables Uvicorn's proxy-header middleware. Uvicorn
    accepts ``X-Forwarded-For`` only from peers trusted by
    ``FORWARDED_ALLOW_IPS`` and exposes the result as ``request.client``.
    """
    if request.client is None:
        return "unknown"
    return normalize_client_ip(request.client.host) or request.client.host


def registration_rate_limit(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """Throttle registration and migration requests per client IP."""
    key = f"register:{_registration_client_ip(request)}"
    if _registration_limiter.allow(
        key,
        limit=settings.registration_rate_limit,
        window_seconds=settings.registration_rate_limit_window_seconds,
    ):
        return
    raise RegistryError(
        ErrorCode.RATE_LIMITED,
        "too many registration or migration requests; retry later",
        status_code=429,
    )
