from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import json
import socket
import threading
import time
from typing import TypeVar
from urllib.parse import urlsplit

import httpcore
import httpx

from registry_api.errors import ErrorCode, RegistryError

MAX_HTTP_PROOF_BYTES = 10 * 1024
MAX_PUBLIC_HTTP_TARGET_ADDRESSES = 8
MAX_DOH_RESPONSE_BYTES = 64 * 1024

_PUBLIC_PROOF_SCHEMES = frozenset({"http", "https"})
_ONION_SUFFIX = ".onion"
_CONNECT_PER_ATTEMPT_TIMEOUT = 3.0
_MAX_TRACKED_PROOF_DOMAINS = 10_000
_ResultT = TypeVar("_ResultT")


@dataclass
class _DomainFetchRecord:
    failed_at: float | None = None
    window_start: float = 0.0
    count: int = 0
    active: int = 0


@dataclass(frozen=True)
class _ProofFetchPermit:
    domain: str
    generation: int


class _ProofFetchPolicy:
    """Cross-request limits for outbound proof fetches (module-level singleton).

    The proof URLs we fetch are attacker-chosen, so without limits a flood of
    registrations could turn the registry into a DDoS source against a
    third-party domain. This policy caps: retries against a recently failed
    domain (cooldown), how many fetches a single domain receives per window,
    and how many outbound fetches run concurrently across requests.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, _DomainFetchRecord] = {}
        self._max_concurrent_fetches = 0
        self._active_fetches = 0
        self._failure_cooldown_seconds = 0.0
        self._quota = 0
        self._quota_window_seconds = 60.0
        self._last_pruned = 0.0
        self._generation = 0

    def configure(
        self,
        *,
        failure_cooldown_seconds: float,
        quota: int,
        quota_window_seconds: float,
        max_concurrent_fetches: int,
    ) -> None:
        with self._lock:
            self._failure_cooldown_seconds = failure_cooldown_seconds
            self._quota = quota
            self._quota_window_seconds = quota_window_seconds
            self._max_concurrent_fetches = max_concurrent_fetches

    def admit(self, domain: str) -> _ProofFetchPermit:
        """Atomically reserve global concurrency and one domain quota unit."""
        with self._lock:
            if (
                self._max_concurrent_fetches > 0
                and self._active_fetches >= self._max_concurrent_fetches
            ):
                raise RegistryError(
                    ErrorCode.RATE_LIMITED,
                    "too many concurrent domain proof fetches; retry later",
                    status_code=429,
                )

            now = time.monotonic()
            self._prune_locked(now)
            record = self._records.get(domain)
            if record is None:
                self._make_record_room_locked(now)
                record = _DomainFetchRecord(window_start=now)
                self._records[domain] = record
            elif now - record.window_start >= self._quota_window_seconds:
                record.window_start = now
                record.count = 0

            if (
                self._failure_cooldown_seconds > 0
                and record.failed_at is not None
                and now - record.failed_at < self._failure_cooldown_seconds
            ):
                raise RegistryError(
                    ErrorCode.RATE_LIMITED,
                    "domain proof fetching for this host is cooling down after a failure; retry later",
                    status_code=429,
                )
            if self._quota > 0 and record.count >= self._quota:
                raise RegistryError(
                    ErrorCode.RATE_LIMITED,
                    "too many domain proof fetches for this host; retry later",
                    status_code=429,
                )

            record.count += 1
            record.active += 1
            self._active_fetches += 1
            generation = self._generation

        return _ProofFetchPermit(
            domain=domain,
            generation=generation,
        )

    def mark_failed(self, permit: _ProofFetchPermit) -> None:
        with self._lock:
            if permit.generation != self._generation:
                return
            now = time.monotonic()
            record = self._records.get(permit.domain)
            if record is not None:
                record.failed_at = now
                self._prune_locked(now)

    def release(self, permit: _ProofFetchPermit) -> None:
        with self._lock:
            if permit.generation != self._generation:
                return
            if self._active_fetches <= 0:
                raise RuntimeError("domain proof fetch permit released more than once")
            record = self._records.get(permit.domain)
            if record is None or record.active <= 0:
                raise RuntimeError(
                    "domain proof fetch permit has no active domain record"
                )
            record.active -= 1
            self._active_fetches -= 1

    def finish(self, permit: _ProofFetchPermit, *, failed: bool) -> None:
        if failed:
            self.mark_failed(permit)
        self.release(permit)

    def is_allowed(self, domain: str) -> bool:
        """Read-only policy check used by tests and diagnostics."""
        with self._lock:
            record = self._records.get(domain)
            if record is None:
                return True
            now = time.monotonic()
            if (
                self._failure_cooldown_seconds > 0
                and record.failed_at is not None
                and now - record.failed_at < self._failure_cooldown_seconds
            ):
                return False
            if (
                self._quota > 0
                and now - record.window_start < self._quota_window_seconds
                and record.count >= self._quota
            ):
                return False
            return True

    def _prune_locked(self, now: float, *, force: bool = False) -> None:
        if not force and now - self._last_pruned < 60.0:
            return
        self._last_pruned = now
        retention = max(
            self._quota_window_seconds, self._failure_cooldown_seconds, 60.0
        )
        stale = [
            domain
            for domain, record in self._records.items()
            if record.active == 0
            and now - max(record.failed_at or 0.0, record.window_start) > retention
        ]
        for domain in stale:
            del self._records[domain]

    def _make_record_room_locked(self, now: float) -> None:
        if len(self._records) < _MAX_TRACKED_PROOF_DOMAINS:
            return
        self._prune_locked(now, force=True)
        if len(self._records) >= _MAX_TRACKED_PROOF_DOMAINS:
            raise RegistryError(
                ErrorCode.RATE_LIMITED,
                "too many domain proof hosts are currently tracked; retry later",
                status_code=429,
            )

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._max_concurrent_fetches = 0
            self._active_fetches = 0
            self._last_pruned = 0.0
            self._generation += 1


_proof_fetch_policy = _ProofFetchPolicy()


def _remaining_time(deadline: float, message: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFIER_UNREACHABLE,
            message,
            status_code=503,
        )
    return remaining


def _require_identity_encoding(
    response: httpx.Response,
    *,
    error: ErrorCode,
    message: str,
    status_code: int,
) -> None:
    """Reject compressed bodies before HTTPX allocates decoded content."""
    content_encoding = response.headers.get("content-encoding", "").strip().lower()
    if content_encoding not in {"", "identity"}:
        raise RegistryError(error, message, status_code=status_code)


def _start_daemon_call(operation: Callable[[], _ResultT]) -> Future[_ResultT]:
    future: Future[_ResultT] = Future()

    def run() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            result = operation()
        except BaseException as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)

    threading.Thread(target=run, name="domain-proof-fetch", daemon=True).start()
    return future


class HttpxProofClient:
    """HTTP proof fetcher hardened against SSRF and third-party abuse.

    Proof URLs are fully attacker-controlled (the registrant chooses the
    domain), so before a fetch the host is resolved once and every address is
    verified to be a global public IP. The actual connection is then pinned to
    those validated addresses: the URL hostname is still used for the ``Host``
    header, TLS SNI and certificate verification, but DNS is never re-resolved
    at connect time, which closes the DNS-rebinding window an attacker could
    otherwise use to point a proof host at a private address.

    Fetches are additionally bounded: a wall-clock deadline covers the whole
    request, the connect phase splits a budget across address attempts, and a
    shared policy caps per-domain volume and concurrency so registrations
    cannot be used to DDoS a third-party domain.
    """

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        dns_over_https_url: str = "https://dns.google/resolve",
        transport: httpx.BaseTransport | None = None,
        enforce_public_http_proof_ips: bool = True,
        domain_fetch_failure_cooldown_seconds: float = 30.0,
        domain_fetch_quota: int = 20,
        domain_fetch_quota_window_seconds: float = 60.0,
        max_concurrent_fetches: int = 16,
    ) -> None:
        self.timeout = timeout
        self.dns_over_https_url = dns_over_https_url
        self.transport = transport
        self.enforce_public_http_proof_ips = enforce_public_http_proof_ips
        _proof_fetch_policy.configure(
            failure_cooldown_seconds=domain_fetch_failure_cooldown_seconds,
            quota=domain_fetch_quota,
            quota_window_seconds=domain_fetch_quota_window_seconds,
            max_concurrent_fetches=max_concurrent_fetches,
        )

    def fetch_text(self, url: str) -> str:
        hostname = urlsplit(url).hostname or "<invalid-host>"
        return self._run_bounded(
            hostname,
            lambda deadline: self._fetch_text(url, deadline),
            timeout_message="HTTP domain proof fetch exceeded the total time budget",
        )

    def _fetch_text(self, url: str, deadline: float) -> str:
        transport: httpx.BaseTransport | None
        if self.enforce_public_http_proof_ips and self.transport is None:
            target = _resolve_public_http_target(url)
            _remaining_time(
                deadline,
                "time budget exhausted before connecting to domain proof host",
            )
            transport = (
                # .onion hosts have no ordinary DNS records; they can only be
                # reached through a Tor-capable proxy if one is configured, and
                # there is no public-IP check that would apply, so the request
                # is attempted with the default transport (which honors proxy
                # environment variables) and otherwise fails resolution.
                None
                if target is None
                else _PinnedIpHTTPTransport(
                    {target.hostname: target.addresses},
                    connect_deadline=deadline,
                    per_attempt_timeout=_CONNECT_PER_ATTEMPT_TIMEOUT,
                )
            )
        else:
            transport = self.transport
        try:
            with httpx.Client(
                timeout=_remaining_time(
                    deadline, "time budget exhausted before fetching domain proof"
                ),
                transport=transport,
                # A custom transport disables environment proxies explicitly.
                # Public proof hosts must be reached directly so the validated
                # IP is the actual peer. The default transport is retained for
                # .onion hosts so a configured Tor-capable proxy can handle them.
                trust_env=transport is None,
                # Redirects are never followed: each hop is an attacker-chosen
                # target that would otherwise bypass the public-IP pinning.
                follow_redirects=False,
            ) as client:
                with client.stream(
                    "GET", url, headers={"Accept-Encoding": "identity"}
                ) as response:
                    _remaining_time(
                        deadline,
                        "HTTP domain proof fetch exceeded the total time budget",
                    )
                    response.raise_for_status()
                    _require_identity_encoding(
                        response,
                        error=ErrorCode.DOMAIN_VERIFICATION_FAILED,
                        message="HTTP domain proof response must not be compressed",
                        status_code=400,
                    )
                    chunks = []
                    total = 0
                    for chunk in response.iter_bytes():
                        _remaining_time(
                            deadline,
                            "HTTP domain proof fetch exceeded the total time budget",
                        )
                        total += len(chunk)
                        if total > MAX_HTTP_PROOF_BYTES:
                            raise RegistryError(
                                ErrorCode.DOMAIN_VERIFICATION_FAILED,
                                "HTTP domain proof response is too large",
                            )
                        chunks.append(chunk)
                    _remaining_time(
                        deadline,
                        "HTTP domain proof fetch exceeded the total time budget",
                    )
                    return b"".join(chunks).decode(response.encoding or "utf-8")
        except httpx.HTTPError as exc:
            raise RegistryError(
                ErrorCode.DOMAIN_VERIFIER_UNREACHABLE,
                "failed to fetch HTTP domain proof",
                status_code=503,
            ) from exc

    def resolve_txt_google(self, domain: str) -> Sequence[str]:
        return self._run_bounded(
            domain,
            lambda deadline: self._resolve_txt_google(domain, deadline),
            timeout_message="DNS-over-HTTPS query exceeded the total time budget",
        )

    def _resolve_txt_google(self, domain: str, deadline: float) -> Sequence[str]:
        try:
            with httpx.Client(
                timeout=_remaining_time(
                    deadline,
                    "time budget exhausted before querying DNS-over-HTTPS resolver",
                ),
                transport=self.transport,
            ) as client:
                with client.stream(
                    "GET",
                    self.dns_over_https_url,
                    params={"name": domain, "type": "TXT"},
                    headers={"Accept-Encoding": "identity"},
                ) as response:
                    _remaining_time(
                        deadline,
                        "DNS-over-HTTPS query exceeded the total time budget",
                    )
                    response.raise_for_status()
                    _require_identity_encoding(
                        response,
                        error=ErrorCode.DOMAIN_VERIFIER_UNREACHABLE,
                        message="DNS-over-HTTPS response must not be compressed",
                        status_code=503,
                    )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        _remaining_time(
                            deadline,
                            "DNS-over-HTTPS query exceeded the total time budget",
                        )
                        body.extend(chunk)
                        if len(body) > MAX_DOH_RESPONSE_BYTES:
                            raise RegistryError(
                                ErrorCode.DOMAIN_VERIFIER_UNREACHABLE,
                                "DNS-over-HTTPS response is too large",
                                status_code=503,
                            )
                    _remaining_time(
                        deadline,
                        "DNS-over-HTTPS query exceeded the total time budget",
                    )
        except httpx.HTTPError as exc:
            raise RegistryError(
                ErrorCode.DOMAIN_VERIFIER_UNREACHABLE,
                "failed to query DNS-over-HTTPS resolver",
                status_code=503,
            ) from exc

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RegistryError(
                ErrorCode.DOMAIN_VERIFIER_UNREACHABLE,
                "DNS-over-HTTPS resolver returned invalid JSON",
                status_code=503,
            ) from exc

        if not isinstance(payload, dict):
            raise RegistryError(
                ErrorCode.DOMAIN_VERIFIER_UNREACHABLE,
                "DNS-over-HTTPS resolver returned an unexpected response format",
                status_code=503,
            )

        answers = payload.get("Answer") or []
        if not isinstance(answers, list):
            raise RegistryError(
                ErrorCode.DOMAIN_VERIFIER_UNREACHABLE,
                "DNS-over-HTTPS resolver returned an unexpected response format",
                status_code=503,
            )

        normalized: list[str] = []
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            data = answer.get("data")
            if not isinstance(data, str):
                continue
            normalized.append(_normalize_google_txt_answer(data))
        return normalized

    def _run_bounded(
        self,
        domain: str,
        operation: Callable[[float], _ResultT],
        *,
        timeout_message: str,
    ) -> _ResultT:
        """Run blocking resolver/client work behind one policy permit.

        The daemon worker lets the request return at the wall-clock deadline
        even when a platform resolver or transport ignores its timeout. A
        timed-out worker retains its concurrency permit until it really exits,
        so stalled work cannot be replaced with an unbounded number of threads.
        """
        policy = _proof_fetch_policy
        permit = policy.admit(domain)
        deadline = time.monotonic() + self.timeout
        try:
            future = _start_daemon_call(lambda: operation(deadline))
        except Exception:
            policy.finish(permit, failed=True)
            raise
        release_on_return = True
        failed = True
        try:
            result = future.result(timeout=max(deadline - time.monotonic(), 0.0))
            failed = False
            return result
        except FutureTimeoutError:
            policy.mark_failed(permit)
            future.add_done_callback(lambda _future: policy.release(permit))
            release_on_return = False
            raise RegistryError(
                ErrorCode.DOMAIN_VERIFIER_UNREACHABLE,
                timeout_message,
                status_code=503,
            ) from None
        finally:
            if release_on_return:
                policy.finish(permit, failed=failed)


@dataclass(frozen=True)
class _PublicHttpTarget:
    hostname: str
    addresses: tuple[str, ...]


class _PinnedIpNetworkBackend(httpcore.SyncBackend):
    """Network backend that connects only to pre-validated public IPs.

    httpcore calls ``connect_tcp`` with the URL hostname; this backend maps
    that hostname to the addresses already resolved and validated as public
    and connects there instead of re-resolving DNS, which an attacker could
    otherwise flip between validation and connection (DNS rebinding).

    The absolute ``connect_deadline`` shares the caller's total request budget
    across address attempts. Each individual attempt is also capped at
    ``per_attempt_timeout`` so a single unreachable address cannot consume the
    whole budget.
    """

    def __init__(
        self,
        host_to_addresses: Mapping[str, Sequence[str]],
        *,
        connect_deadline: float,
        per_attempt_timeout: float,
    ) -> None:
        self._host_to_addresses = host_to_addresses
        self._connect_deadline = connect_deadline
        self._per_attempt_timeout = per_attempt_timeout

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        addresses = self._host_to_addresses.get(host)
        if not addresses:
            raise httpcore.ConnectError(
                f"host {host!r} has no validated public IP addresses"
            )

        last_error: Exception | None = None
        for address in addresses:
            remaining = self._connect_deadline - time.monotonic()
            if remaining <= 0:
                raise httpcore.ConnectTimeout(
                    "timed out connecting to validated HTTP proof addresses"
                )
            attempt_timeout = remaining if timeout is None else min(timeout, remaining)
            attempt_timeout = min(attempt_timeout, self._per_attempt_timeout)
            try:
                return super().connect_tcp(
                    address,
                    port,
                    timeout=attempt_timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore.ConnectTimeout as exc:
                last_error = exc
            except httpcore.ConnectError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise httpcore.ConnectError(
            f"host {host!r} has no validated public IP addresses"
        )


_HTTPCORE_EXCEPTIONS: tuple[tuple[type[Exception], type[httpx.HTTPError]], ...] = (
    (httpcore.ConnectTimeout, httpx.ConnectTimeout),
    (httpcore.ReadTimeout, httpx.ReadTimeout),
    (httpcore.WriteTimeout, httpx.WriteTimeout),
    (httpcore.PoolTimeout, httpx.PoolTimeout),
    (httpcore.TimeoutException, httpx.TimeoutException),
    (httpcore.ConnectError, httpx.ConnectError),
    (httpcore.ReadError, httpx.ReadError),
    (httpcore.WriteError, httpx.WriteError),
    (httpcore.ProxyError, httpx.ProxyError),
    (httpcore.NetworkError, httpx.NetworkError),
    (httpcore.LocalProtocolError, httpx.LocalProtocolError),
    (httpcore.RemoteProtocolError, httpx.RemoteProtocolError),
    (httpcore.UnsupportedProtocol, httpx.UnsupportedProtocol),
    (httpcore.ProtocolError, httpx.ProtocolError),
)


@contextmanager
def _map_httpcore_exceptions() -> Iterator[None]:
    try:
        yield
    except Exception as exc:
        for source, destination in _HTTPCORE_EXCEPTIONS:
            if isinstance(exc, source):
                raise destination(str(exc)) from exc
        raise


class _PinnedIpResponseStream(httpx.SyncByteStream):
    def __init__(self, stream: Iterable[bytes]) -> None:
        self._stream = stream

    def __iter__(self) -> Iterator[bytes]:
        with _map_httpcore_exceptions():
            yield from self._stream

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if close is not None:
            with _map_httpcore_exceptions():
                close()


class _PinnedIpHTTPTransport(httpx.BaseTransport):
    """HTTPTransport whose connections are pinned to pre-validated public IPs.

    The connection pool is built with :class:`_PinnedIpNetworkBackend`, which
    maps each URL hostname to the validated addresses. The hostname is still
    used for the ``Host`` header and TLS SNI / certificate verification, so
    only the DNS step is replaced, never the origin identity.
    """

    def __init__(
        self,
        host_to_addresses: Mapping[str, Sequence[str]],
        *,
        connect_deadline: float,
        per_attempt_timeout: float,
    ) -> None:
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            network_backend=_PinnedIpNetworkBackend(
                host_to_addresses,
                connect_deadline=connect_deadline,
                per_attempt_timeout=per_attempt_timeout,
            ),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if not isinstance(request.stream, httpx.SyncByteStream):
            raise TypeError("pinned HTTP transport requires a synchronous stream")

        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        with _map_httpcore_exceptions():
            core_response = self._pool.handle_request(core_request)

        if not isinstance(core_response.stream, Iterable):
            raise TypeError("pinned HTTP transport received an asynchronous stream")
        return httpx.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            stream=_PinnedIpResponseStream(core_response.stream),
            extensions=core_response.extensions,
        )

    def close(self) -> None:
        with _map_httpcore_exceptions():
            self._pool.close()


def _normalize_google_txt_answer(data: str) -> str:
    chunks = []
    current = []
    in_quote = False
    escaped = False
    for char in data:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            if in_quote:
                chunks.append("".join(current))
                current = []
            in_quote = not in_quote
        elif in_quote:
            current.append(char)
        elif not char.isspace():
            current.append(char)
    if current:
        chunks.append("".join(current))
    return "".join(chunks) if chunks else data


def _reject_private_http_target(url: str) -> None:
    """Raise if ``url`` is not a safe public HTTP proof target."""
    _resolve_public_http_target(url)


def _resolve_public_http_target(url: str) -> _PublicHttpTarget | None:
    """Resolve ``url`` and verify every address is a global public IP.

    Returns the hostname and the resolved (deduplicated) addresses, or
    ``None`` for ``.onion`` hosts, whose addresses cannot be resolved over
    ordinary DNS. Raises :class:`RegistryError` when the URL is malformed or
    any resolved address is non-public.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFICATION_FAILED,
            "HTTP domain proof URL is invalid",
        ) from None
    if parts.scheme not in _PUBLIC_PROOF_SCHEMES:
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFICATION_FAILED,
            "HTTP domain proof URL must use http or https",
        )
    if parts.hostname is None:
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFICATION_FAILED,
            "HTTP domain proof URL must include a host",
        )
    try:
        port = parts.port
    except ValueError:
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFICATION_FAILED,
            "HTTP domain proof URL has an invalid port",
        ) from None

    if parts.hostname.endswith(_ONION_SUFFIX):
        return None

    try:
        resolved = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFIER_UNREACHABLE,
            "failed to resolve HTTP domain proof host",
            status_code=503,
        ) from exc

    addresses: list[str] = []
    for result in resolved:
        host = str(result[4][0])
        ip = ipaddress.ip_address(host)
        if not _is_public_unicast_ip(ip):
            raise RegistryError(
                ErrorCode.DOMAIN_VERIFICATION_FAILED,
                "HTTP domain proof host resolves to a non-public IP address",
                {"host": parts.hostname},
            )
        addresses.append(host)

    unique_addresses = tuple(dict.fromkeys(addresses))
    return _PublicHttpTarget(
        parts.hostname,
        unique_addresses[:MAX_PUBLIC_HTTP_TARGET_ADDRESSES],
    )


def _is_public_unicast_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return whether an address is globally routable unicast.

    ``ipaddress.is_global`` also accepts multicast and deprecated IPv6
    site-local addresses on supported Python versions, so security-sensitive
    target validation must reject special-purpose categories explicitly.
    """
    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
        and not getattr(ip, "is_site_local", False)
    )
