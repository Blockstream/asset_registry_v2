from fastapi.testclient import TestClient

from registry_api.api.v2 import ensure_genesis_admin
from registry_api.canonical_json import canonical_json
from registry_api.main import create_app


ASSET_ID = "aa909f1b00000000000000000000000000000000000000000000000000000000"
PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"


def v2_registration() -> dict:
    return {
        "asset_id": ASSET_ID,
        "contract": {
            "entity": {"domain": "proof.example.com"},
            "initial_issuer_pubkey": PUBKEY,
            "name": "Example Asset",
            "precision": 8,
            "ticker": "EXAMPLE",
            "version": 2,
        },
    }


def legacy_registration() -> dict:
    return {
        "asset_id": ASSET_ID,
        "contract": {
            "entity": {"domain": "proof.example.com"},
            "issuer_pubkey": PUBKEY,
            "name": "Legacy Asset",
            "precision": 0,
            "ticker": "LEGACY",
            "version": 0,
        },
    }


def test_v2_registration_rejects_nul_in_contract_name() -> None:
    request = v2_registration()
    request["contract"]["name"] = "bad\x00name"

    response = TestClient(create_app()).post("/v2/assets", json=request)

    assert response.status_code == 422
    assert "name must not contain NUL characters" in response.text


def test_legacy_registration_rejects_nul_in_contract_name() -> None:
    request = legacy_registration()
    request["contract"]["name"] = "bad\x00name"

    response = TestClient(create_app()).post("/", json=request)

    assert response.status_code == 422
    assert "name must not contain NUL characters" in response.text


def test_legacy_registration_rejects_nul_in_nested_contract_extra() -> None:
    request = legacy_registration()
    request["contract"]["extra"] = {"nested": "bad\x00value"}

    response = TestClient(create_app()).post("/", json=request)

    assert response.status_code == 422
    assert "\\u0000" in response.text


def test_admin_annotations_reject_nul_in_notes() -> None:
    app = create_app()
    app.dependency_overrides[ensure_genesis_admin] = lambda: None
    action = {
        "signing_context": "liquid-asset-registry-admin-action-v1",
        "actor_pubkey": PUBKEY,
        "operation": "update_admin_annotations",
        "asset_id": ASSET_ID,
        "timestamp": "2026-07-08T12:00:00Z",
        "nonce": "nul-notes",
        "changes": {"admin_notes": "bad\x00notes"},
    }

    response = TestClient(app).put(
        f"/v2/admin/assets/{ASSET_ID}/annotations",
        content=canonical_json(action),
        headers={
            "content-type": "application/json",
            "Asset-Registry-Admin-Signature": "not-checked-before-validation",
        },
    )

    assert response.status_code == 422
    assert "admin_notes must not contain NUL characters" in response.text
