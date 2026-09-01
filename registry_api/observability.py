import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from registry_api.client_address import normalize_client_ip

REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LENGTH = 128


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "request_id",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "client",
            "forwarded_for_present",
            "client_forwarded_match",
            "error",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


def request_id_from_request(request: Request) -> str:
    incoming = request.headers.get(REQUEST_ID_HEADER)
    request_id = incoming.strip() if incoming and incoming.strip() else ""
    return request_id[:MAX_REQUEST_ID_LENGTH] if request_id else str(uuid.uuid4())


class RequestLoggingMiddleware:
    """Log requests without Starlette's task-spawning HTTP middleware wrapper."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        logger = logging.getLogger("registry_api.request")
        request_id = request_id_from_request(request)
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            logger.exception(
                "request_failed",
                extra=_request_log_extra(
                    request, request_id, 500, duration_ms, error=type(exc).__name__
                ),
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        logger.info(
            "request_completed",
            extra=_request_log_extra(request, request_id, status_code, duration_ms),
        )


def _request_log_extra(
    request: Request,
    request_id: str,
    status_code: int,
    duration_ms: float,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    forwarded_for_present, client_forwarded_match = _forwarded_client_indicator(request)
    raw_client = request.client.host if request.client else None
    client = (normalize_client_ip(raw_client) or raw_client) if raw_client else None
    return {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "client": client,
        "forwarded_for_present": forwarded_for_present,
        "client_forwarded_match": client_forwarded_match,
        "error": error,
    }


def _forwarded_client_indicator(request: Request) -> tuple[bool, bool]:
    """Return a non-sensitive signal for checking proxy resolution in logs.

    ``client_forwarded_match`` does not make a trust decision; Uvicorn already
    did that before invoking the application. It only reports whether the
    resolved ``request.client`` address appears anywhere in the forwarded
    chain, without logging the chain itself.
    """
    forwarded_fields = request.headers.getlist("X-Forwarded-For")
    if not forwarded_fields:
        return False, False

    client = normalize_client_ip(request.client.host) if request.client else None
    if client is None:
        return True, False

    forwarded_addresses = (
        normalize_client_ip(value)
        for field in forwarded_fields
        for value in field.split(",")
    )
    return True, any(address == client for address in forwarded_addresses)
