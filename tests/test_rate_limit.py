import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from registry_api.api.v2 import ensure_genesis_admin
from registry_api.main import create_app
from registry_api.rate_limit import (
    _registration_limiter,
    _registration_scope_client_ip,
)
from registry_api.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def reset_limiter():
    _registration_limiter.reset()
    get_settings.cache_clear()
    yield
    _registration_limiter.reset()
    get_settings.cache_clear()


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


def test_registration_rate_limit_normalizes_asgi_client_host() -> None:
    assert (
        _registration_scope_client_ip({"client": ("198.51.100.1:1234", 50000)})
        == "198.51.100.1"
    )


def test_migration_rate_limit_runs_before_genesis_bootstrap() -> None:
    settings = Settings(registration_rate_limit=1)
    app = create_app(settings=settings)
    bootstrap_calls = []
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[ensure_genesis_admin] = lambda: bootstrap_calls.append(
        True
    )
    client = TestClient(app)
    path = f"/v2/assets/{'a' * 64}/migrate"

    assert client.post(path, json={}).status_code == 422
    assert client.post(path, json={}).status_code == 429
    assert bootstrap_calls == [True]


def test_invalid_migration_asset_id_is_still_rate_limited() -> None:
    settings = Settings(registration_rate_limit=1)
    app = create_app(settings=settings)
    bootstrap_calls = []
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[ensure_genesis_admin] = lambda: bootstrap_calls.append(
        True
    )
    client = TestClient(app)
    path = "/v2/assets/not-an-asset-id/migrate"

    assert client.post(path, json={}).status_code == 422
    assert client.post(path, json={}).status_code == 429
    assert bootstrap_calls == [True]


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/v2/assets",
        "/v2/assets/not-an-asset-id/migrate",
    ],
)
def test_rate_limit_matches_uvicorn_root_path_scope(path: str) -> None:
    settings = Settings(registration_rate_limit=1)
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[ensure_genesis_admin] = lambda: None

    async def uvicorn_root_path_app(scope, receive, send) -> None:
        if scope["type"] == "http":
            scope = {
                **scope,
                "root_path": "/registry",
                "path": f"/registry{scope['path']}",
                "raw_path": b"/registry" + scope["raw_path"],
            }
        await app(scope, receive, send)

    client = TestClient(uvicorn_root_path_app)

    assert client.post(path, json={}).status_code == 422
    response = client.post(path, json={})

    assert response.status_code == 429
    assert response.json()["error"] == "rate_limited"


def test_rate_limit_rejects_before_json_body_validation(monkeypatch) -> None:
    settings = Settings(registration_rate_limit=1)
    app = create_app(settings=settings)
    parse_calls = []

    def unexpected_parse(_payload: bytes) -> object:
        parse_calls.append(True)
        raise AssertionError("rate-limited body must not be parsed")

    client = TestClient(app)
    assert client.post("/v2/assets", json={}).status_code == 422
    monkeypatch.setattr("registry_api.security.parse_json_bytes", unexpected_parse)

    response = client.post(
        "/v2/assets",
        content=b'{"malformed":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 429
    assert response.json()["error"] == "rate_limited"
    assert parse_calls == []


def test_rate_limit_uses_client_resolved_by_uvicorn_proxy_middleware() -> None:
    settings = Settings(registration_rate_limit=1)
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
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
