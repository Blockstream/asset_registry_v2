from starlette.requests import Request

from registry_api.observability import _request_log_extra


def _request(
    client: str | None, headers: list[tuple[bytes, bytes]] | None = None
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/health",
            "raw_path": b"/health",
            "query_string": b"",
            "headers": headers or [],
            "client": (client, 1234) if client is not None else None,
            "server": ("registry.example", 443),
        }
    )


def test_request_log_marks_direct_client_without_forwarded_header() -> None:
    extra = _request_log_extra(_request("198.51.100.1"), "request-id", 200, 1.0)

    assert extra["client"] == "198.51.100.1"
    assert extra["forwarded_for_present"] is False
    assert extra["client_forwarded_match"] is False


def test_request_log_marks_resolved_client_in_forwarded_chain() -> None:
    extra = _request_log_extra(
        _request(
            "198.51.100.1",
            [
                (b"x-forwarded-for", b"192.0.2.10"),
                (b"x-forwarded-for", b"198.51.100.1, 10.0.0.2"),
            ],
        ),
        "request-id",
        200,
        1.0,
    )

    assert extra["forwarded_for_present"] is True
    assert extra["client_forwarded_match"] is True


def test_request_log_marks_unmatched_forwarded_chain() -> None:
    extra = _request_log_extra(
        _request(
            "10.0.0.2",
            [(b"x-forwarded-for", b"198.51.100.1")],
        ),
        "request-id",
        200,
        1.0,
    )

    assert extra["forwarded_for_present"] is True
    assert extra["client_forwarded_match"] is False


def test_request_log_normalizes_forwarded_host_port() -> None:
    extra = _request_log_extra(
        _request(
            "198.51.100.1:1234",
            [(b"x-forwarded-for", b"198.51.100.1:1234")],
        ),
        "request-id",
        200,
        1.0,
    )

    assert extra["client"] == "198.51.100.1"
    assert extra["client_forwarded_match"] is True
