import http.server
import gzip
import socket
import threading
import time

import httpcore
import httpx
import pytest

from registry_api.errors import RegistryError
from registry_api.http_clients import (
    HttpxProofClient,
    MAX_DOH_RESPONSE_BYTES,
    MAX_HTTP_PROOF_BYTES,
    MAX_PUBLIC_HTTP_TARGET_ADDRESSES,
    _PublicHttpTarget,
    _normalize_google_txt_answer,
    _proof_fetch_policy,
    _reject_private_http_target,
    _resolve_public_http_target,
)


def test_google_txt_answer_normalization() -> None:
    assert (
        _normalize_google_txt_answer('"liquid-asset-" "verification=abc,XYZ"')
        == "liquid-asset-verification=abc,XYZ"
    )
    assert _normalize_google_txt_answer('"plain"') == "plain"


def test_fetch_text_uses_httpx_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://proof.example.com/.well-known/proof"
        return httpx.Response(200, text="ok")

    client = HttpxProofClient(transport=httpx.MockTransport(handler))
    assert client.fetch_text("https://proof.example.com/.well-known/proof") == "ok"


def test_fetch_text_rejects_oversized_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_HTTP_PROOF_BYTES + 1))

    client = HttpxProofClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RegistryError) as exc_info:
        client.fetch_text("https://proof.example.com/.well-known/proof")

    assert exc_info.value.error == "domain_verification_failed"


def test_fetch_text_rejects_compressed_response_before_decoding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            content=gzip.compress(b"x" * (MAX_HTTP_PROOF_BYTES + 1)),
            headers={"content-encoding": "gzip"},
        )

    client = HttpxProofClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RegistryError) as exc_info:
        client.fetch_text("https://proof.example.com/.well-known/proof")

    assert exc_info.value.error == "domain_verification_failed"
    assert exc_info.value.status_code == 400


def test_resolve_txt_google_parses_answers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["type"] == "TXT"
        return httpx.Response(200, json={"Answer": [{"data": '"first" "second"'}]})

    client = HttpxProofClient(transport=httpx.MockTransport(handler))
    assert client.resolve_txt_google("proof.example.com") == ["firstsecond"]


def test_resolve_txt_google_rejects_oversized_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_DOH_RESPONSE_BYTES + 1))

    client = HttpxProofClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RegistryError) as exc_info:
        client.resolve_txt_google("proof.example.com")

    assert exc_info.value.error == "domain_verifier_unreachable"
    assert exc_info.value.status_code == 503


def test_resolve_txt_google_rejects_compressed_response_before_decoding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            content=gzip.compress(b'{"Answer": []}'),
            headers={"content-encoding": "gzip"},
        )

    client = HttpxProofClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RegistryError) as exc_info:
        client.resolve_txt_google("proof.example.com")

    assert exc_info.value.error == "domain_verifier_unreachable"
    assert exc_info.value.status_code == 503


def test_resolve_txt_google_rejects_invalid_json() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"this is not json{{{")

    client = HttpxProofClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RegistryError) as exc_info:
        client.resolve_txt_google("proof.example.com")

    assert exc_info.value.error == "domain_verifier_unreachable"
    assert exc_info.value.status_code == 503


def test_resolve_txt_google_skips_malformed_answers() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Answer": [
                    {"data": "valid"},
                    {"data": None},
                    42,
                    {"data": 7},
                ]
            },
        )

    client = HttpxProofClient(transport=httpx.MockTransport(handler))
    assert client.resolve_txt_google("proof.example.com") == ["valid"]


def test_fetch_text_enforces_total_time_budget() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        time.sleep(1.2)
        return httpx.Response(200, text="ok")

    client = HttpxProofClient(timeout=0.5, transport=httpx.MockTransport(handler))
    started = time.monotonic()
    with pytest.raises(RegistryError) as exc_info:
        client.fetch_text("https://proof.example.com/.well-known/proof")

    assert exc_info.value.error == "domain_verifier_unreachable"
    assert exc_info.value.status_code == 503
    assert time.monotonic() - started < 0.8


def test_fetch_text_blocks_domain_after_recent_failure(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("registry_api.http_clients.time.monotonic", lambda: now[0])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = HttpxProofClient(timeout=0.5, transport=httpx.MockTransport(handler))
    with pytest.raises(RegistryError) as exc_info:
        client.fetch_text("https://victim.example/.well-known/proof")
    assert exc_info.value.error == "domain_verifier_unreachable"
    assert exc_info.value.status_code == 503

    with pytest.raises(RegistryError) as exc_info:
        client.fetch_text("https://victim.example/.well-known/proof")
    assert exc_info.value.error == "rate_limited"


def test_pinned_backend_respects_connect_budget(monkeypatch) -> None:
    import httpcore

    from registry_api.http_clients import _PinnedIpNetworkBackend

    attempts: list[tuple[str, float]] = []

    def slow_connect(address, timeout, source_address=None):
        attempts.append((address, timeout))
        time.sleep(0.3)
        raise socket.timeout("connect timed out")

    monkeypatch.setattr(
        "registry_api.http_clients.socket.create_connection", slow_connect
    )

    backend = _PinnedIpNetworkBackend(
        {"proof.example.com": ("192.0.2.1", "192.0.2.2")},
        connect_deadline=time.monotonic() + 0.5,
        per_attempt_timeout=0.4,
    )
    with pytest.raises(httpcore.ConnectTimeout):
        backend.connect_tcp("proof.example.com", 443, timeout=5)

    assert len(attempts) == 2
    # First attempt gets the full per-attempt budget.
    assert attempts[0][1] <= 0.4 + 0.01
    # Second attempt is capped by the remaining overall budget.
    assert attempts[1][1] <= attempts[0][1] - 0.2 + 0.05


@pytest.fixture(autouse=True)
def reset_proof_fetch_policy():
    _proof_fetch_policy.reset()
    yield
    _proof_fetch_policy.reset()


def test_proof_fetch_policy_blocks_domain_after_failure(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("registry_api.http_clients.time.monotonic", lambda: now[0])
    _proof_fetch_policy.configure(
        failure_cooldown_seconds=60,
        quota=0,
        quota_window_seconds=60,
        max_concurrent_fetches=4,
    )

    permit = _proof_fetch_policy.admit("victim.example")
    _proof_fetch_policy.finish(permit, failed=True)
    assert not _proof_fetch_policy.is_allowed("victim.example")
    with pytest.raises(RegistryError) as exc_info:
        _proof_fetch_policy.admit("victim.example")
    assert exc_info.value.error == "rate_limited"

    now[0] += 61
    permit = _proof_fetch_policy.admit("victim.example")
    _proof_fetch_policy.finish(permit, failed=False)


def test_proof_fetch_policy_enforces_domain_quota(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("registry_api.http_clients.time.monotonic", lambda: now[0])
    _proof_fetch_policy.configure(
        failure_cooldown_seconds=0,
        quota=2,
        quota_window_seconds=60,
        max_concurrent_fetches=4,
    )

    first = _proof_fetch_policy.admit("quota.example")
    second = _proof_fetch_policy.admit("quota.example")
    with pytest.raises(RegistryError) as exc_info:
        _proof_fetch_policy.admit("quota.example")
    assert exc_info.value.error == "rate_limited"
    _proof_fetch_policy.finish(first, failed=False)
    _proof_fetch_policy.finish(second, failed=False)

    now[0] += 61
    permit = _proof_fetch_policy.admit("quota.example")
    _proof_fetch_policy.finish(permit, failed=False)


def test_proof_fetch_policy_caps_concurrent_fetches() -> None:
    _proof_fetch_policy.configure(
        failure_cooldown_seconds=0,
        quota=0,
        quota_window_seconds=60,
        max_concurrent_fetches=1,
    )

    permit = _proof_fetch_policy.admit("first.example")
    with pytest.raises(RegistryError) as exc_info:
        _proof_fetch_policy.admit("second.example")
    assert exc_info.value.error == "rate_limited"
    _proof_fetch_policy.finish(permit, failed=False)
    permit = _proof_fetch_policy.admit("second.example")
    _proof_fetch_policy.finish(permit, failed=False)


def test_proof_fetch_policy_reconfigures_concurrency_limit() -> None:
    _proof_fetch_policy.configure(
        failure_cooldown_seconds=0,
        quota=0,
        quota_window_seconds=60,
        max_concurrent_fetches=1,
    )
    first = _proof_fetch_policy.admit("first.example")

    _proof_fetch_policy.configure(
        failure_cooldown_seconds=0,
        quota=0,
        quota_window_seconds=60,
        max_concurrent_fetches=2,
    )
    second = _proof_fetch_policy.admit("second.example")
    with pytest.raises(RegistryError):
        _proof_fetch_policy.admit("third.example")

    _proof_fetch_policy.finish(first, failed=False)
    third = _proof_fetch_policy.admit("third.example")
    _proof_fetch_policy.finish(second, failed=False)
    _proof_fetch_policy.finish(third, failed=False)


def test_proof_fetch_policy_fails_closed_at_record_cap(
    monkeypatch,
) -> None:
    monkeypatch.setattr("registry_api.http_clients._MAX_TRACKED_PROOF_DOMAINS", 2)
    now = [1000.0]
    monkeypatch.setattr("registry_api.http_clients.time.monotonic", lambda: now[0])
    _proof_fetch_policy.configure(
        failure_cooldown_seconds=0,
        quota=1,
        quota_window_seconds=60,
        max_concurrent_fetches=0,
    )

    for domain in ("oldest.example", "newer.example"):
        permit = _proof_fetch_policy.admit(domain)
        _proof_fetch_policy.finish(permit, failed=False)

    with pytest.raises(RegistryError) as exc_info:
        _proof_fetch_policy.admit("newest.example")
    assert exc_info.value.error == "rate_limited"
    assert len(_proof_fetch_policy._records) == 2
    assert set(_proof_fetch_policy._records) == {
        "oldest.example",
        "newer.example",
    }

    with pytest.raises(RegistryError):
        _proof_fetch_policy.admit("oldest.example")

    now[0] += 61
    permit = _proof_fetch_policy.admit("newest.example")
    _proof_fetch_policy.finish(permit, failed=False)
    assert set(_proof_fetch_policy._records) == {"newest.example"}


def test_proof_fetch_policy_rejects_new_domain_when_all_records_are_active(
    monkeypatch,
) -> None:
    monkeypatch.setattr("registry_api.http_clients._MAX_TRACKED_PROOF_DOMAINS", 1)
    _proof_fetch_policy.configure(
        failure_cooldown_seconds=0,
        quota=0,
        quota_window_seconds=60,
        max_concurrent_fetches=0,
    )

    permit = _proof_fetch_policy.admit("active.example")
    try:
        with pytest.raises(RegistryError) as exc_info:
            _proof_fetch_policy.admit("other.example")
        assert exc_info.value.error == "rate_limited"
        assert len(_proof_fetch_policy._records) == 1
    finally:
        _proof_fetch_policy.finish(permit, failed=False)


def test_dns_resolution_holds_concurrency_slot_until_worker_exits(monkeypatch) -> None:
    release_resolver = threading.Event()
    resolver_calls = []

    def slow_resolver(_url: str):
        resolver_calls.append(True)
        release_resolver.wait(timeout=2)
        raise RegistryError("domain_verifier_unreachable", "resolution failed")

    monkeypatch.setattr(
        "registry_api.http_clients._resolve_public_http_target", slow_resolver
    )
    client = HttpxProofClient(
        timeout=0.05,
        domain_fetch_failure_cooldown_seconds=0,
        domain_fetch_quota=0,
        max_concurrent_fetches=1,
    )

    try:
        with pytest.raises(RegistryError) as exc_info:
            client.fetch_text("https://slow.example/proof")
        assert exc_info.value.error == "domain_verifier_unreachable"

        with pytest.raises(RegistryError) as exc_info:
            client.fetch_text("https://other.example/proof")
        assert exc_info.value.error == "rate_limited"
        assert len(resolver_calls) == 1
    finally:
        release_resolver.set()


def test_http_client_wraps_http_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = HttpxProofClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RegistryError) as exc_info:
        client.fetch_text("https://proof.example.com")

    assert exc_info.value.error == "domain_verifier_unreachable"
    assert exc_info.value.status_code == 503


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "224.0.0.1",
        "fec0::1",
        "ff0e::1",
    ],
)
def test_rejects_non_public_unicast_http_proof_target(
    monkeypatch, address: str
) -> None:
    monkeypatch.setattr(
        "registry_api.http_clients.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, (address, 443))],
    )

    with pytest.raises(RegistryError) as exc_info:
        _reject_private_http_target("https://proof.example.com/.well-known/proof")

    assert exc_info.value.error == "domain_verification_failed"


def test_allows_global_http_proof_target(monkeypatch) -> None:
    monkeypatch.setattr(
        "registry_api.http_clients.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    _reject_private_http_target("https://proof.example.com/.well-known/proof")


def test_onion_http_proof_target_skips_public_ip_check(monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("unexpected DNS resolution")

    monkeypatch.setattr("registry_api.http_clients.socket.getaddrinfo", fail_if_called)

    _reject_private_http_target(
        "http://proofaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion/.well-known/proof"
    )


def test_resolve_public_http_target_rejects_non_http_scheme(monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("unexpected DNS resolution")

    monkeypatch.setattr("registry_api.http_clients.socket.getaddrinfo", fail_if_called)

    with pytest.raises(RegistryError) as exc_info:
        _resolve_public_http_target("ftp://proof.example.com/.well-known/proof")

    assert exc_info.value.error == "domain_verification_failed"


def test_resolve_public_http_target_rejects_invalid_port() -> None:
    with pytest.raises(RegistryError) as exc_info:
        _resolve_public_http_target("https://proof.example.com:99999/.well-known/proof")

    assert exc_info.value.error == "domain_verification_failed"


def test_resolve_onion_target_rejects_invalid_port() -> None:
    with pytest.raises(RegistryError) as exc_info:
        _resolve_public_http_target("http://proof.example.onion:99999/proof")

    assert exc_info.value.error == "domain_verification_failed"


def test_resolve_public_http_target_caps_retained_addresses(monkeypatch) -> None:
    addresses = [f"8.8.8.{index}" for index in range(1, 12)]
    monkeypatch.setattr(
        "registry_api.http_clients.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (None, None, None, None, (address, 443)) for address in addresses
        ],
    )

    target = _resolve_public_http_target("https://proof.example.com/proof")

    assert target is not None
    assert target.addresses == tuple(addresses[:MAX_PUBLIC_HTTP_TARGET_ADDRESSES])


def test_resolve_public_http_target_validates_addresses_beyond_cap(
    monkeypatch,
) -> None:
    addresses = [
        *(f"8.8.8.{index}" for index in range(1, 10)),
        "127.0.0.1",
    ]
    monkeypatch.setattr(
        "registry_api.http_clients.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (None, None, None, None, (address, 443)) for address in addresses
        ],
    )

    with pytest.raises(RegistryError) as exc_info:
        _resolve_public_http_target("https://proof.example.com/proof")

    assert exc_info.value.error == "domain_verification_failed"


@pytest.fixture()
def local_proof_server():
    """HTTP server bound to loopback that returns a fixed proof body."""
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), lambda *a, **kw: _ProofHandler(*a, **kw)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


class _ProofHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_fetch_text_pins_connection_to_validated_ip(
    monkeypatch, local_proof_server
) -> None:
    """The fetch must connect to the validated IP, never re-resolve DNS."""
    host, port = local_proof_server.server_address
    monkeypatch.setattr(
        "registry_api.http_clients._resolve_public_http_target",
        lambda url: _PublicHttpTarget("unresolvable.invalid", (host,)),
    )

    client = HttpxProofClient()
    # .invalid is a reserved TLD that never resolves; success proves the
    # connection was pinned to the validated address instead of re-resolving.
    assert (
        client.fetch_text(f"http://unresolvable.invalid:{port}/.well-known/proof")
        == "ok"
    )


def test_fetch_text_ignores_environment_proxy_for_public_target(
    monkeypatch, local_proof_server
) -> None:
    host, port = local_proof_server.server_address
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setattr(
        "registry_api.http_clients._resolve_public_http_target",
        lambda url: _PublicHttpTarget("unresolvable.invalid", (host,)),
    )

    client = HttpxProofClient()
    assert client.fetch_text(f"http://unresolvable.invalid:{port}/proof") == "ok"


def test_fetch_text_retains_environment_proxy_support_for_onion(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}
    real_client = httpx.Client

    def make_client(*args, **kwargs):
        observed.update(kwargs)
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(200, text="ok")
        )
        return real_client(*args, **kwargs)

    monkeypatch.setattr("registry_api.http_clients.httpx.Client", make_client)

    client = HttpxProofClient()
    assert client.fetch_text("http://proof.example.onion/proof") == "ok"
    assert observed["trust_env"] is True


def test_fetch_text_fails_closed_for_host_without_validated_ips(monkeypatch) -> None:
    monkeypatch.setattr(
        "registry_api.http_clients._resolve_public_http_target",
        lambda url: _PublicHttpTarget("unresolvable.invalid", ()),
    )

    client = HttpxProofClient()
    with pytest.raises(RegistryError) as exc_info:
        client.fetch_text("http://unresolvable.invalid/.well-known/proof")

    assert exc_info.value.error == "domain_verifier_unreachable"


def test_pinned_backend_never_connects_to_dns_resolved_host(
    monkeypatch, local_proof_server
) -> None:
    """Even when the host resolves elsewhere via DNS, only the pinned IP is used."""
    from registry_api.http_clients import _PinnedIpNetworkBackend

    host, port = local_proof_server.server_address
    resolved_hosts: list[str] = []

    def record_resolved_host(*args, **kwargs):
        # socket.create_connection still calls getaddrinfo for IP literals to
        # pick the address family, but must never be asked to resolve the URL
        # hostname again (that would reopen the DNS-rebinding window).
        resolved_hosts.append(str(args[0]))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (args[0], args[1]))]

    monkeypatch.setattr(
        "registry_api.http_clients.socket.getaddrinfo", record_resolved_host
    )

    backend = _PinnedIpNetworkBackend(
        {"proof.example.com": (host,)},
        connect_deadline=time.monotonic() + 5,
        per_attempt_timeout=3.0,
    )
    stream = backend.connect_tcp("proof.example.com", port, timeout=5)
    try:
        assert stream.get_extra_info("socket").getpeername()[0] == host
    finally:
        stream.close()
    assert "proof.example.com" not in resolved_hosts
    assert host in resolved_hosts


def test_pinned_backend_fails_closed_for_unknown_host() -> None:
    from registry_api.http_clients import _PinnedIpNetworkBackend

    backend = _PinnedIpNetworkBackend(
        {"proof.example.com": ("93.184.216.34",)},
        connect_deadline=time.monotonic() + 5,
        per_attempt_timeout=3.0,
    )
    with pytest.raises(httpcore.ConnectError):
        backend.connect_tcp("other.example.net", 443, timeout=5)


def test_pinned_backend_shares_timeout_across_addresses(monkeypatch) -> None:
    from registry_api.http_clients import _PinnedIpNetworkBackend

    now = [100.0]
    attempted_timeouts: list[float | None] = []

    def fail_connect(
        self,
        host,
        port,
        timeout=None,
        local_address=None,
        socket_options=None,
    ):
        attempted_timeouts.append(timeout)
        now[0] += timeout or 0
        raise httpcore.ConnectTimeout("unreachable")

    monkeypatch.setattr("registry_api.http_clients.time.monotonic", lambda: now[0])
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", fail_connect)
    backend = _PinnedIpNetworkBackend(
        {
            "proof.example.com": (
                "93.184.216.34",
                "93.184.216.35",
                "93.184.216.36",
            )
        },
        connect_deadline=112.0,
        per_attempt_timeout=10.0,
    )

    with pytest.raises(httpcore.ConnectTimeout):
        backend.connect_tcp("proof.example.com", 443, timeout=10)

    assert attempted_timeouts == [10, 2]
