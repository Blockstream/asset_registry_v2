import httpx
from fastapi import Response

from registry_api.api import legacy as legacy_api
from registry_api.errors import ErrorCode, RegistryError
from registry_api.schemas import LegacyAssetRequest, LegacyDeletionRequest
from registry_api.settings import Settings
from registry_api.shadow import (
    ShadowOutcome,
    ensure_legacy_deregistration_written,
    ensure_legacy_registration_written,
)


def legacy_request() -> LegacyAssetRequest:
    return LegacyAssetRequest.model_validate(
        {
            "asset_id": "dd909f1b00000000000000000000000000000000000000000000000000000000",
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "issuer_pubkey": "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65",
                "name": "Shadow Asset",
                "precision": 0,
                "ticker": "SHDW",
                "version": 0,
            },
        }
    )


def legacy_deletion_request() -> LegacyDeletionRequest:
    return LegacyDeletionRequest(signature="delete-signature")


def test_shadow_disabled_skips_forwarding() -> None:
    settings = Settings(
        legacy_base_url="https://legacy.example.com", legacy_shadow_write=False
    )

    outcome = ensure_legacy_registration_written(settings, legacy_request())

    assert outcome.classification == "shadow_disabled"


def test_deregistration_shadow_disabled_skips_forwarding() -> None:
    settings = Settings(
        legacy_base_url="https://legacy.example.com", legacy_shadow_write=False
    )

    outcome = ensure_legacy_deregistration_written(
        settings, legacy_request().asset_id, legacy_deletion_request()
    )

    assert outcome.classification == "shadow_disabled"


def test_deregistration_shadow_forwards_delete(monkeypatch) -> None:
    original_client = httpx.Client
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="Asset deleted")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    settings = Settings(
        legacy_base_url="https://legacy.example.com", legacy_shadow_write=True
    )

    outcome = ensure_legacy_deregistration_written(
        settings, legacy_request().asset_id, legacy_deletion_request()
    )

    assert outcome.classification == "legacy_delete_succeeded"
    assert requests[0].method == "DELETE"
    assert (
        str(requests[0].url)
        == f"https://legacy.example.com/{legacy_request().asset_id}"
    )
    assert requests[0].read() == b'{"signature":"delete-signature"}'


def test_deregistration_legacy_500_returns_failed_outcome_without_confirmation(
    monkeypatch,
) -> None:
    original_client = httpx.Client
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, text="known legacy failure")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    settings = Settings(
        legacy_base_url="https://legacy.example.com",
        legacy_shadow_write=True,
    )

    outcome = ensure_legacy_deregistration_written(
        settings, legacy_request().asset_id, legacy_deletion_request()
    )

    assert outcome.classification == "legacy_delete_failed"
    assert outcome.legacy_status_code == 500
    assert outcome.legacy_response == "known legacy failure"
    assert [request.method for request in requests] == ["DELETE"]


def test_deregistration_unreachable_returns_outcome_without_raising(
    monkeypatch,
) -> None:
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    settings = Settings(
        legacy_base_url="https://legacy.example.com",
        legacy_shadow_write=True,
    )

    outcome = ensure_legacy_deregistration_written(
        settings, legacy_request().asset_id, legacy_deletion_request()
    )

    assert outcome.classification == "legacy_unreachable"
    assert outcome.legacy_status_code is None
    assert outcome.error == "connection failed"


def test_legacy_500_returns_failed_outcome_without_confirmation(monkeypatch) -> None:
    original_client = httpx.Client
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, text="known legacy failure")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    settings = Settings(
        legacy_base_url="https://legacy.example.com",
        legacy_shadow_write=True,
    )

    outcome = ensure_legacy_registration_written(settings, legacy_request())

    assert outcome.classification == "legacy_write_failed"
    assert outcome.legacy_status_code == 500
    assert outcome.legacy_response == "known legacy failure"
    assert [request.method for request in requests] == ["POST"]


def test_registration_unreachable_returns_outcome_without_raising(monkeypatch) -> None:
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    settings = Settings(
        legacy_base_url="https://legacy.example.com",
        legacy_shadow_write=True,
    )

    outcome = ensure_legacy_registration_written(settings, legacy_request())

    assert outcome.classification == "legacy_unreachable"
    assert outcome.legacy_status_code is None
    assert outcome.error == "connection failed"


def test_legacy_registration_conflict_is_swallowed_when_shadow_write_is_enabled(
    monkeypatch,
) -> None:
    shadow_calls = []

    def local_register(*args, **kwargs) -> dict:
        raise RegistryError(ErrorCode.ASSET_CONFLICT, "asset conflict", status_code=409)

    def shadow_write(settings: Settings, request: LegacyAssetRequest) -> ShadowOutcome:
        shadow_calls.append(request.asset_id)
        return ShadowOutcome("legacy_write_failed", 409, {"error": "already exists"})

    monkeypatch.setattr(legacy_api, "register_legacy_asset", local_register)
    monkeypatch.setattr(legacy_api, "ensure_legacy_registration_written", shadow_write)
    response = Response()

    result = legacy_api.register_asset_legacy_root(
        legacy_request(),
        response,
        db=object(),
        settings=Settings(
            legacy_shadow_write=True, legacy_base_url="https://legacy.example.com"
        ),
        _rate_limit=None,
    )

    assert result["asset_id"] == legacy_request().asset_id
    assert response.status_code == 409
    assert shadow_calls == [legacy_request().asset_id]


def test_legacy_registration_conflict_is_not_swallowed_when_shadow_write_is_disabled(
    monkeypatch,
) -> None:
    def local_register(*args, **kwargs) -> dict:
        raise RegistryError(ErrorCode.ASSET_CONFLICT, "asset conflict", status_code=409)

    monkeypatch.setattr(legacy_api, "register_legacy_asset", local_register)

    try:
        legacy_api.register_asset_legacy_root(
            legacy_request(),
            Response(),
            db=object(),
            settings=Settings(
                legacy_shadow_write=False, legacy_base_url="https://legacy.example.com"
            ),
            _rate_limit=None,
        )
    except RegistryError as exc:
        assert exc.error == ErrorCode.ASSET_CONFLICT
    else:
        raise AssertionError("expected RegistryError")


def test_legacy_delete_local_failure_is_swallowed_when_shadow_write_is_enabled(
    monkeypatch,
) -> None:
    shadow_calls = []

    def local_delete(*args, **kwargs) -> str:
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND, "asset not found", status_code=404
        )

    def shadow_delete(
        settings: Settings, asset_id: str, request: LegacyDeletionRequest
    ) -> ShadowOutcome:
        shadow_calls.append(asset_id)
        return ShadowOutcome("legacy_delete_succeeded", 200, "Asset deleted")

    monkeypatch.setattr(legacy_api, "deregister_legacy_asset", local_delete)
    monkeypatch.setattr(
        legacy_api, "ensure_legacy_deregistration_written", shadow_delete
    )

    response = legacy_api.delete_asset_legacy_root(
        legacy_request().asset_id,
        legacy_deletion_request(),
        db=object(),
        settings=Settings(
            legacy_shadow_write=True, legacy_base_url="https://legacy.example.com"
        ),
    )

    assert response.status_code == 200
    assert response.body == b"Asset deleted"
    assert shadow_calls == [legacy_request().asset_id]
