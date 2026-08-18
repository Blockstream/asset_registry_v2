import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from registry_api.api.v2 import ensure_genesis_admin
from registry_api.canonical_json import (
    action_hash,
    canonical_json,
    contract_hash,
    parse_json_bytes,
    require_canonical_json,
)
from registry_api.errors import RegistryError
from registry_api.main import create_app


ASSET_ID = "aa909f1b00000000000000000000000000000000000000000000000000000000"
INVALID_JSON_PAYLOADS = (
    pytest.param(b'{"value":}', id="malformed"),
    pytest.param(b"\xff", id="non-utf-8"),
    pytest.param(b'{"value":"\\ud800"}', id="lone-high-surrogate"),
    pytest.param(b'{"value":"\\udc00"}', id="lone-low-surrogate"),
    pytest.param(b'{"value":NaN}', id="nan"),
    pytest.param(b'{"value":Infinity}', id="infinity"),
    pytest.param(b'{"value":1e400}', id="float-overflow"),
)
SIGNED_ACTION_ENDPOINTS = (
    pytest.param("POST", f"/v2/assets/{ASSET_ID}/actions", "Asset-Registry-Signature", id="issuer-action"),
    pytest.param("POST", f"/v2/assets/{ASSET_ID}/migrate", "Asset-Registry-Admin-Signature", id="migration"),
    pytest.param("POST", "/v2/admin/actions", "Asset-Registry-Admin-Signature", id="admin-lifecycle"),
    pytest.param(
        "PUT",
        f"/v2/admin/assets/{ASSET_ID}/annotations",
        "Asset-Registry-Admin-Signature",
        id="admin-annotations",
    ),
    pytest.param(
        "POST",
        f"/v2/admin/assets/{ASSET_ID}/actions",
        "Asset-Registry-Admin-Signature",
        id="admin-asset-action",
    ),
)


def test_canonical_json_sorts_keys_and_removes_whitespace() -> None:
    assert canonical_json({"b": 2, "a": {"d": 4, "c": 3}}) == '{"a":{"c":3,"d":4},"b":2}'


def test_action_hash_uses_canonical_json_without_byte_reversal() -> None:
    action = {"b": 2, "a": {"d": 4, "c": 3}}
    expected = hashlib.sha256(b'{"a":{"c":3,"d":4},"b":2}').hexdigest()

    assert action_hash(action) == expected
    assert action_hash({"a": {"c": 3, "d": 4}, "b": 2}) == expected


def test_require_canonical_json_rejects_non_canonical_payload() -> None:
    payload = b'{"b": 2, "a": 1}'

    with pytest.raises(RegistryError) as exc_info:
        require_canonical_json(payload)

    assert exc_info.value.error == "non_canonical_payload"
    assert exc_info.value.details["canonical_payload"] == '{"a":1,"b":2}'


@pytest.mark.parametrize("payload", INVALID_JSON_PAYLOADS)
def test_require_canonical_json_rejects_invalid_json(payload: bytes) -> None:
    with pytest.raises(RegistryError) as exc_info:
        require_canonical_json(payload)

    assert exc_info.value.error == "invalid_json"
    assert exc_info.value.status_code == 400
    assert exc_info.value.details is None


def test_parse_json_bytes_accepts_valid_surrogate_pair() -> None:
    assert parse_json_bytes(b'{"value":"\\ud83d\\ude00"}') == {"value": "😀"}


@pytest.mark.parametrize("payload", INVALID_JSON_PAYLOADS)
@pytest.mark.parametrize(("method", "path", "signature_header"), SIGNED_ACTION_ENDPOINTS)
def test_signed_action_endpoints_return_400_for_invalid_json(
    payload: bytes,
    method: str,
    path: str,
    signature_header: str,
) -> None:
    app = create_app()
    app.dependency_overrides[ensure_genesis_admin] = lambda: None

    response = TestClient(app).request(
        method,
        path,
        content=payload,
        headers={"content-type": "application/json", signature_header: "not-checked-before-json-validation"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_json",
        "message": "request body must be valid UTF-8 JSON without non-finite numbers",
    }


def test_legacy_registration_returns_400_for_infinity_in_contract_extra_field() -> None:
    payload = (
        f'{{"asset_id":"{ASSET_ID}","contract":{{"name":"x","ticker":"INF","precision":2,'
        '"version":0,"entity":{"domain":"ex.com"},"whatever":Infinity},'
        '"domain_verification_method":"http"}'
    ).encode()

    response = TestClient(create_app()).post(
        "/",
        content=payload,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_json",
        "message": "request body must be valid UTF-8 JSON without non-finite numbers",
    }


def test_contract_hash_matches_known_fixture_value() -> None:
    contract = {
        "entity": {"domain": "test.dev"},
        "issuer_pubkey": "0304781a856a8779cf93316bd2162d038ecaf923fa1d9d9c44c1f0fb37fba11a16",
        "name": "Testcoin",
        "precision": 0,
        "ticker": "TEST",
        "version": 0,
    }
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    expected = hashlib.sha256(canonical).digest()[::-1].hex()

    assert contract_hash(contract) == expected
    assert contract_hash(contract) == "f5a62f84b91f5e5fc9716c210bb03a5f3d63ea1832f4b8d522000adbc92d548b"
