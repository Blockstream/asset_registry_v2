from copy import deepcopy

from registry_api.canonical_json import contract_hash
from registry_api.chain import derive_asset_id
from scripts.audit_legacy_contracts import audit_legacy_contracts

PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
PREVOUT_TXID = "11" * 32
PREVOUT_VOUT = 1


def legacy_item() -> tuple[str, dict]:
    contract = {
        "entity": {"domain": "legacy.example.com"},
        "issuer_pubkey": PUBKEY,
        "name": "Audited legacy asset",
        "precision": 0,
        "ticker": "AUDIT",
        "version": 0,
    }
    hash_hex = contract_hash(contract)
    asset_id = derive_asset_id(PREVOUT_TXID, PREVOUT_VOUT, hash_hex)
    return asset_id, {
        "asset_id": asset_id,
        "collection": None,
        "contract": contract,
        "contract_hash": hash_hex,
        "issuance_prevout": {"txid": PREVOUT_TXID, "vout": PREVOUT_VOUT},
    }


def test_audit_legacy_contracts_verifies_hash_and_asset_id() -> None:
    asset_id, item = legacy_item()

    summary = audit_legacy_contracts({asset_id: item})

    assert summary.valid
    assert summary.verified == 1
    assert summary.contract_collection_absent == 1
    assert summary.top_level_null_collection_only == 1


def test_audit_legacy_contracts_reports_contract_mismatch() -> None:
    asset_id, item = legacy_item()
    tampered = deepcopy(item)
    tampered["contract"]["collection"] = None

    summary = audit_legacy_contracts({asset_id: tampered})

    assert not summary.valid
    assert summary.verified == 0
    assert summary.findings[0].reason == (
        "canonical contract hash does not match preserved contract_hash"
    )
