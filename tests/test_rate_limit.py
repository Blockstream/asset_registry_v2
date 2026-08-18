from fastapi.testclient import TestClient
from starlette.datastructures import Address
from starlette.datastructures import Headers
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import pytest

from registry_api.api.v2 import ensure_genesis_admin
from registry_api.errors import RegistryError
from registry_api.main import create_app
from registry_api.rate_limit import _registration_limiter, registration_rate_limit
from registry_api.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def reset_limiter():
    _registration_limiter.reset()
    get_settings.cache_clear()
    yield
    _registration_limiter.reset()
    get_settings.cache_clear()


def _request(host: str, *, forwarded_for: str | None = None) -> object:
    headers = Headers(
        {"x-forwarded-for": forwarded_for} if forwarded_for is not None else {}
    )
    return type(
        "FakeRequest",
        (),
        {"client": Address(host, 1234), "headers": headers},
    )()


def test_limiter_allows_up_to_limit() -> None:
    assert _registration_limiter.allow("key", limit=2, window_seconds=60) is True
    assert _registration_limiter.allow("key", limit=2, window_seconds=60) is True
    assert _registration_limiter.allow("key", limit=2, window_seconds=60) is False


def test_limiter_window_expiry(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("registry_api.rate_limit.time.monotonic", lambda: now[0])

    assert _registration_limiter.allow("key", limit=1, window_seconds=60) is True
    assert _registration_limiter.allow("key", limit=1, window_seconds=60) is False

    now[0] += 61
    assert _registration_limiter.allow("key", limit=1, window_seconds=60) is True


def test_limiter_zero_limit_disables_throttling() -> None:
    assert _registration_limiter.allow("key", limit=0, window_seconds=60) is True


def test_limiter_prunes_inactive_client_keys(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("registry_api.rate_limit.time.monotonic", lambda: now[0])

    assert _registration_limiter.allow("old", limit=1, window_seconds=60)
    now[0] += 61
    assert _registration_limiter.allow("current", limit=1, window_seconds=60)

    assert "old" not in _registration_limiter._events
    assert "current" in _registration_limiter._events


def test_limiter_fails_closed_for_new_key_at_capacity(monkeypatch) -> None:
    monkeypatch.setattr("registry_api.rate_limit._MAX_TRACKED_CLIENTS", 2)

    assert _registration_limiter.allow("oldest", limit=1, window_seconds=60)
    assert _registration_limiter.allow("active", limit=1, window_seconds=60)
    assert not _registration_limiter.allow("newest", limit=1, window_seconds=60)

    assert len(_registration_limiter._events) == 2
    assert set(_registration_limiter._events) == {"oldest", "active"}
    assert _registration_limiter.allow("oldest", limit=1, window_seconds=60) is False
    assert _registration_limiter.allow("active", limit=1, window_seconds=60) is False


def test_limiter_reclaims_expired_keys_at_capacity(monkeypatch) -> None:
    monkeypatch.setattr("registry_api.rate_limit._MAX_TRACKED_CLIENTS", 2)
    now = [1000.0]
    monkeypatch.setattr("registry_api.rate_limit.time.monotonic", lambda: now[0])

    assert _registration_limiter.allow("first", limit=1, window_seconds=60)
    assert _registration_limiter.allow("second", limit=1, window_seconds=60)

    now[0] += 61
    assert _registration_limiter.allow("third", limit=1, window_seconds=60)
    assert set(_registration_limiter._events) == {"third"}


def test_registration_rate_limit_raises_after_budget() -> None:
    settings = Settings(
        registration_rate_limit=2, registration_rate_limit_window_seconds=60
    )
    request = _request("203.0.113.9")

    registration_rate_limit(request, settings)
    registration_rate_limit(request, settings)
    with pytest.raises(RegistryError) as exc_info:
        registration_rate_limit(request, settings)

    assert exc_info.value.error == "rate_limited"
    assert exc_info.value.status_code == 429


def test_registration_rate_limit_uses_asgi_resolved_client() -> None:
    settings = Settings(registration_rate_limit=1)

    registration_rate_limit(
        _request("198.51.100.1", forwarded_for="203.0.113.10"), settings
    )
    with pytest.raises(RegistryError):
        registration_rate_limit(
            _request("198.51.100.1", forwarded_for="203.0.113.11"),
            settings,
        )

    registration_rate_limit(
        _request("198.51.100.2", forwarded_for="203.0.113.10"), settings
    )


def test_registration_rate_limit_normalizes_forwarded_host_port() -> None:
    settings = Settings(registration_rate_limit=1)

    registration_rate_limit(_request("198.51.100.1:1234"), settings)
    with pytest.raises(RegistryError):
        registration_rate_limit(_request("198.51.100.1:5678"), settings)


def test_migration_rate_limit_runs_before_genesis_bootstrap() -> None:
    app = create_app()
    bootstrap_calls = []
    app.dependency_overrides[get_settings] = lambda: Settings(registration_rate_limit=1)
    app.dependency_overrides[ensure_genesis_admin] = lambda: bootstrap_calls.append(
        True
    )
    client = TestClient(app)
    path = f"/v2/assets/{'a' * 64}/migrate"

    assert client.post(path, json={}).status_code == 422
    assert client.post(path, json={}).status_code == 429
    assert bootstrap_calls == [True]


def test_rate_limit_uses_client_resolved_by_uvicorn_proxy_middleware() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(registration_rate_limit=1)
    app.dependency_overrides[ensure_genesis_admin] = lambda: None
    wrapped_app = ProxyHeadersMiddleware(app, trusted_hosts="testclient")
    client = TestClient(wrapped_app)
    path = f"/v2/assets/{'a' * 64}/migrate"

    assert (
        client.post(
            path, json={}, headers={"X-Forwarded-For": "198.51.100.1"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, json={}, headers={"X-Forwarded-For": "198.51.100.2"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, json={}, headers={"X-Forwarded-For": "198.51.100.1"}
        ).status_code
        == 429
    )
