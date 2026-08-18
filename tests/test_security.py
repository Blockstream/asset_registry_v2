import asyncio
import json
from collections import deque
from collections.abc import Iterable
from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from registry_api.security import (
    JsonDepthLimitMiddleware,
    RequestBodySizeLimitMiddleware,
    RequestJsonTooDeep,
    _JsonDepthValidator,
    _content_length,
    _should_check_json_depth,
)


async def _echo_body_app(_scope: Scope, receive: Receive, send: Send) -> None:
    body_chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        body_chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break

    response_body = json.dumps({"body": b"".join(body_chunks).decode()}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(response_body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": response_body})


async def _noop_app(_scope: Scope, _receive: Receive, send: Send) -> None:
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def _run_middleware(
    messages: Iterable[Message],
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    max_body_bytes: int = 1024,
    max_json_depth: int = 2,
) -> tuple[int, dict[str, Any]]:
    pending = deque(messages)
    sent: list[Message] = []
    app = JsonDepthLimitMiddleware(
        _echo_body_app,
        max_json_depth=max_json_depth,
    )
    middleware = RequestBodySizeLimitMiddleware(app, max_body_bytes=max_body_bytes)
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers if headers is not None else [(b"content-type", b"application/json")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive() -> Message:
        if pending:
            return pending.popleft()
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))

    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return status, json.loads(body or b"{}")


def test_json_depth_middleware_replays_chunked_body_after_validation() -> None:
    status, payload = _run_middleware(
        [
            {"type": "http.request", "body": b'{"a":', "more_body": True},
            {"type": "http.request", "body": b"1}", "more_body": False},
        ]
    )

    assert status == 200
    assert payload == {"body": '{"a":1}'}


def test_json_depth_middleware_rejects_depth_across_chunks() -> None:
    status, payload = _run_middleware(
        [
            {"type": "http.request", "body": b'{"a":', "more_body": True},
            {"type": "http.request", "body": b'{"b":1}}', "more_body": False},
        ],
        max_json_depth=1,
    )

    assert status == 400
    assert payload["error"] == "json_depth_exceeded"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b'{"value":}', id="malformed"),
        pytest.param(b"\xff", id="non-utf-8"),
        pytest.param(b'{"value":"\\ud800"}', id="lone-high-surrogate"),
        pytest.param(b'{"value":"\\udc00"}', id="lone-low-surrogate"),
        pytest.param(b'{"value":NaN}', id="nan"),
        pytest.param(b'{"value":Infinity}', id="infinity"),
        pytest.param(b'{"value":1e400}', id="float-overflow"),
    ],
)
def test_json_middleware_rejects_invalid_json(body: bytes) -> None:
    status, payload = _run_middleware(
        [{"type": "http.request", "body": body, "more_body": False}],
    )

    assert status == 400
    assert payload == {
        "error": "invalid_json",
        "message": "request body must be valid UTF-8 JSON without non-finite numbers",
    }


def test_json_middleware_accepts_valid_surrogate_pair() -> None:
    status, payload = _run_middleware(
        [{"type": "http.request", "body": b'{"value":"\\ud83d\\ude00"}', "more_body": False}],
    )

    assert status == 200
    assert payload == {"body": '{"value":"\\ud83d\\ude00"}'}


def test_json_middleware_still_validates_json_when_depth_limit_is_disabled() -> None:
    status, payload = _run_middleware(
        [{"type": "http.request", "body": b'{"value":Infinity}', "more_body": False}],
        max_json_depth=0,
    )

    assert status == 400
    assert payload["error"] == "invalid_json"


def test_json_depth_validator_ignores_braces_inside_split_string() -> None:
    validator = _JsonDepthValidator(max_depth=1)

    validator.feed(b'{"a":"{{')
    validator.feed(b'{{"}')

    assert validator.depth == 0
    assert validator.in_string is False
    assert validator.escape is False


def test_json_depth_validator_handles_split_escaped_quote() -> None:
    validator = _JsonDepthValidator(max_depth=1)

    validator.feed(b'{"a":"\\')
    validator.feed(b'""}')

    assert validator.depth == 0
    assert validator.in_string is False
    assert validator.escape is False


def test_json_depth_validator_raises_when_nested_delimiter_crosses_chunk_boundary() -> None:
    validator = _JsonDepthValidator(max_depth=1)
    validator.feed(b'{"a":')

    with pytest.raises(RequestJsonTooDeep):
        validator.feed(b"[")


def test_request_limit_header_matching_is_case_insensitive() -> None:
    scope: Scope = {
        "type": "http",
        "headers": [(b"Content-Type", b"Application/Problem+JSON; charset=utf-8"), (b"Content-Length", b"12")],
    }

    assert _should_check_json_depth(scope) is True
    assert _content_length(scope) == 12


def test_json_depth_check_matches_fastapi_json_default_for_missing_content_type() -> None:
    scope: Scope = {"type": "http", "headers": [(b"Content-Length", b"12")]}

    assert _should_check_json_depth(scope) is True


def test_json_depth_check_matches_fastapi_json_default_for_transfer_encoding() -> None:
    scope: Scope = {"type": "http", "headers": [(b"Transfer-Encoding", b"chunked")]}

    assert _should_check_json_depth(scope) is True


def test_json_depth_check_skips_explicit_non_json_content_type() -> None:
    scope: Scope = {"type": "http", "headers": [(b"Content-Type", b"text/plain"), (b"Content-Length", b"12")]}

    assert _should_check_json_depth(scope) is False


def test_json_depth_middleware_rejects_transfer_encoded_nested_json_without_content_length() -> None:
    status, payload = _run_middleware(
        [
            {"type": "http.request", "body": b'{"a":', "more_body": True},
            {"type": "http.request", "body": b'{"b":1}}', "more_body": False},
        ],
        headers=[(b"transfer-encoding", b"chunked")],
        max_json_depth=1,
    )

    assert status == 400
    assert payload["error"] == "json_depth_exceeded"


def test_non_json_transfer_encoded_body_skips_depth_check_but_still_has_size_limit() -> None:
    status, payload = _run_middleware(
        [
            {"type": "http.request", "body": b"x" * 8, "more_body": True},
            {"type": "http.request", "body": b"x" * 9, "more_body": False},
        ],
        headers=[(b"content-type", b"text/plain"), (b"transfer-encoding", b"chunked")],
        max_body_bytes=16,
        max_json_depth=1,
    )

    assert status == 413
    assert payload["error"] == "request_body_too_large"


def test_request_body_size_limit_middleware_rejects_negative_limit() -> None:
    with pytest.raises(ValueError, match="max_body_bytes"):
        RequestBodySizeLimitMiddleware(_noop_app, max_body_bytes=-1)


def test_json_depth_limit_middleware_rejects_negative_limit() -> None:
    with pytest.raises(ValueError, match="max_json_depth"):
        JsonDepthLimitMiddleware(_noop_app, max_json_depth=-1)


def test_create_app_installs_size_limit_outside_json_depth_limit() -> None:
    from registry_api.main import create_app

    middleware_names = [middleware.cls.__name__ for middleware in create_app().user_middleware]

    assert middleware_names[:4] == [
        "BaseHTTPMiddleware",
        "RequestBodySizeLimitMiddleware",
        "JsonDepthLimitMiddleware",
        "GZipMiddleware",
    ]
