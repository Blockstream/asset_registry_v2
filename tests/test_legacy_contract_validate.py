from fastapi.testclient import TestClient

from registry_api.canonical_json import contract_hash
from registry_api.main import create_app


PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"


def legacy_contract() -> dict:
    return {
        "entity": {"domain": "proof.example.com"},
        "issuer_pubkey": PUBKEY,
        "name": "Legacy Asset",
        "precision": 0,
        "ticker": "LEGACY",
        "version": 0,
    }


def test_legacy_contract_validate_endpoint_accepts_matching_contract_hash() -> None:
    client = TestClient(create_app())
    contract = legacy_contract()

    response = client.post(
        "/contract/validate",
        json={"contract": contract, "contract_hash": contract_hash(contract)},
    )

    assert response.status_code == 200
    assert response.text == "valid"
    assert response.headers["content-type"].startswith("text/plain")


def test_legacy_contract_validate_endpoint_rejects_hash_mismatch() -> None:
    client = TestClient(create_app())
    contract = legacy_contract()

    response = client.post(
        "/contract/validate",
        json={"contract": contract, "contract_hash": "0" * 64},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "validation_error"
    assert "contract hash mismatch" in response.json()["message"]


def test_legacy_contract_validate_endpoint_rejects_invalid_contract_fields() -> None:
    client = TestClient(create_app())
    contract = legacy_contract()
    contract["version"] = 1

    response = client.post(
        "/contract/validate",
        json={"contract": contract, "contract_hash": contract_hash(contract)},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "contract", "version"]
