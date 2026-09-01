from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from fastapi import Request
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from registry_api.canonical_json import parse_json_bytes
from registry_api.errors import RegistryError


class RequestBodyTooLarge(Exception):
    pass


class RequestJsonTooDeep(Exception):
    pass


DOUBLE_QUOTE_BYTE = b'"'[0]
BACKSLASH_BYTE = b"\\"[0]
OPEN_OBJECT_BYTE = b"{"[0]
CLOSE_OBJECT_BYTE = b"}"[0]
OPEN_ARRAY_BYTE = b"["[0]
CLOSE_ARRAY_BYTE = b"]"[0]
OPEN_CONTAINER_BYTES = (OPEN_OBJECT_BYTE, OPEN_ARRAY_BYTE)
CLOSE_CONTAINER_BYTES = (CLOSE_OBJECT_BYTE, CLOSE_ARRAY_BYTE)

_PARSED_JSON_SCOPE_KEY = "registry_api.parsed_json"
_JSON_BODY_SCOPE_KEY = "registry_api.json_body"
_UNSET_JSON = object()


class CachedJsonAPIRoute(APIRoute):
    """Reuse JSON already validated by :class:`JsonDepthLimitMiddleware`."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def cached_json_route_handler(request: Request) -> Response:
            if _PARSED_JSON_SCOPE_KEY in request.scope:
                # FastAPI reads request.body() and request.json() before it solves
                # dependencies. Seed both Starlette request caches so the strict
                # middleware parse is the only parse and buffer pass.
                request._body = request.scope[_JSON_BODY_SCOPE_KEY]
                request._json = request.scope[_PARSED_JSON_SCOPE_KEY]
            return await route_handler(request)

        return cached_json_route_handler


class RequestBodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        if max_body_bytes < 0:
            raise ValueError("max_body_bytes must be greater than or equal to 0")
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_body_bytes <= 0:
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > self.max_body_bytes:
            await _too_large_response(self.max_body_bytes)(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await _too_large_response(self.max_body_bytes)(scope, receive, send)


class JsonDepthLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_json_depth: int) -> None:
        if max_json_depth < 0:
            raise ValueError("max_json_depth must be greater than or equal to 0")
        self.app = app
        self.max_json_depth = max_json_depth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _should_check_json_depth(scope):
            await self.app(scope, receive, send)
            return

        try:
            messages, body, parsed = await self._validated_json_messages(receive)
        except RequestJsonTooDeep:
            await _too_deep_response(self.max_json_depth)(scope, receive, send)
            return
        except RegistryError as exc:
            await _invalid_json_response(exc)(scope, receive, send)
            return

        if parsed is not _UNSET_JSON:
            scope[_JSON_BODY_SCOPE_KEY] = body
            scope[_PARSED_JSON_SCOPE_KEY] = parsed

        async def replay_receive() -> Message:
            if messages:
                return messages.popleft()
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)

    async def _validated_json_messages(
        self, receive: Receive
    ) -> tuple[deque[Message], bytes, Any]:
        messages: deque[Message] = deque()
        body_chunks: list[bytes] = []
        depth_validator = _JsonDepthValidator(self.max_json_depth) if self.max_json_depth > 0 else None
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                return messages, b"", _UNSET_JSON

            body = message.get("body", b"")
            body_chunks.append(body)
            if depth_validator is not None:
                depth_validator.feed(body)

            if not message.get("more_body", False):
                joined_body = b"".join(body_chunks)
                return messages, joined_body, parse_json_bytes(joined_body)


def _content_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _should_check_json_depth(scope: Scope) -> bool:
    for key, value in scope.get("headers") or []:
        if key.lower() != b"content-type":
            continue
        media_type = value.split(b";", 1)[0].strip().lower()
        return media_type == b"application/json" or media_type.endswith(b"+json")
    content_length = _content_length(scope)
    if content_length is not None:
        return content_length > 0
    return _has_transfer_encoding(scope)


def _has_transfer_encoding(scope: Scope) -> bool:
    for key, value in scope.get("headers") or []:
        if key.lower() == b"transfer-encoding" and value.strip().lower() != b"identity":
            return True
    return False


def _too_large_response(max_body_bytes: int) -> Callable[[Scope, Receive, Send], Awaitable[Any]]:
    return JSONResponse(
        status_code=413,
        content={
            "error": "request_body_too_large",
            "message": f"request body must not exceed {max_body_bytes} bytes",
        },
    )


def _too_deep_response(max_json_depth: int) -> Callable[[Scope, Receive, Send], Awaitable[Any]]:
    return JSONResponse(
        status_code=400,
        content={
            "error": "json_depth_exceeded",
            "message": f"JSON request body nesting depth must not exceed {max_json_depth}",
        },
    )


def _invalid_json_response(exc: RegistryError) -> Callable[[Scope, Receive, Send], Awaitable[Any]]:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.error,
            "message": exc.message,
        },
    )


class _JsonDepthValidator:
    def __init__(self, max_depth: int) -> None:
        self.max_depth = max_depth
        self.depth = 0
        self.in_string = False
        self.escape = False

    def feed(self, chunk: bytes) -> None:
        for byte in chunk:
            if self.in_string:
                if self.escape:
                    self.escape = False
                elif byte == BACKSLASH_BYTE:
                    self.escape = True
                elif byte == DOUBLE_QUOTE_BYTE:
                    self.in_string = False
                continue

            if byte == DOUBLE_QUOTE_BYTE:
                self.in_string = True
            elif byte in OPEN_CONTAINER_BYTES:
                self.depth += 1
                if self.depth > self.max_depth:
                    raise RequestJsonTooDeep
            elif byte in CLOSE_CONTAINER_BYTES and self.depth > 0:
                self.depth -= 1
