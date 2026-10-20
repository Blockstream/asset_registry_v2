import json
import os

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from registry_api.issuance import IssuanceMetadata, IssuancePrevout, IssuanceTxin
from registry_api.legacy_assets import get_legacy_asset, list_legacy_assets_json_bytes
from registry_api.legacy_import import import_legacy_assets
from registry_api.migration import migrate_legacy_asset_to_v2
from registry_api.models import Action, Asset, AssetSerializedFragment
from registry_api.registration import register_legacy_asset
from registry_api.schemas import LegacyAssetRequest, RegisterAssetRequest
from registry_api.v2_assets import (
    all_v2_assets_json_bytes,
    all_v2_assets_payload,
    get_v2_asset,
    register_v2_asset,
    search_v2_assets,
)

PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
REFERENCES = {
    "issuance_txin": {"txid": "ab" * 32, "vin": 0},
    "issuance_prevout": {"txid": "cd" * 32, "vout": 4294967295},
}


@pytest.mark.parametrize("index", [True, False, -1, 4294967296, 1.5, "0", None])
@pytest.mark.parametrize(
    "model,index_key", [(IssuanceTxin, "vin"), (IssuancePrevout, "vout")]
)
def test_reference_rejects_invalid_index(model, index_key, index) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"txid": "ab" * 32, index_key: index})


def test_reference_normalizes_hash_and_accepts_zero() -> None:
    assert (
        IssuanceTxin(txid="AB" * 32, vin=0).model_dump() == REFERENCES["issuance_txin"]
    )


@pytest.fixture()
def db():
    url = os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ASSET_REGISTRY_TEST_DATABASE_URL is required")
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            bind=connection, join_transaction_mode="create_savepoint", autoflush=False
        ) as session:
            yield session
        transaction.rollback()
    engine.dispose()


def _request(version):
    contract = {
        "entity": {"domain": "issuance.example.com"},
        "name": "Issuance References",
        "ticker": "REF",
        "precision": 0,
        "version": version,
        "issuer_pubkey" if version == 0 else "initial_issuer_pubkey": PUBKEY,
    }
    model = LegacyAssetRequest if version == 0 else RegisterAssetRequest
    return model.model_validate({"asset_id": "fa" * 32, "contract": contract})


class VerifiedIssuance:
    def verify_issuance_commitment(self, commitment):
        assert commitment.asset_id == "fa" * 32
        return IssuanceMetadata.from_payload(REFERENCES)


@pytest.mark.parametrize("version", [0, 2])
def test_registration_persists_and_serves_references_everywhere(db, version) -> None:
    request = _request(version)
    register = register_legacy_asset if version == 0 else register_v2_asset
    response = register(
        db, request, enforce_chain_verification=True, chain_verifier=VerifiedIssuance()
    )
    asset_id = request.asset_id
    asset = db.scalar(select(Asset).where(Asset.asset_id == asset_id))
    assert asset.issuance_vin == 0
    assert asset.issuance_prevout_vout == 4294967295
    assert asset.contract_extra_fields == {}
    if version == 0:
        assert response["issuance_txin"] == REFERENCES["issuance_txin"]
        migrate_legacy_asset_to_v2(db, asset_id)
    else:
        assert response.issuance_prevout.model_dump() == REFERENCES["issuance_prevout"]

    def assert_responses():
        payloads = [
            get_legacy_asset(db, asset_id),
            json.loads(list_legacy_assets_json_bytes(db))[asset_id],
            get_v2_asset(db, asset_id).model_dump(mode="json"),
            search_v2_assets(db).items[0].model_dump(mode="json"),
            json.loads(all_v2_assets_json_bytes(db))[asset_id],
            all_v2_assets_payload(db)[asset_id],
        ]
        for payload in payloads:
            for key, reference in REFERENCES.items():
                assert payload[key] == reference
                assert key not in payload["contract"]

    assert_responses()
    fragment = db.get(AssetSerializedFragment, asset.asset_uuid)
    db.delete(fragment)
    db.flush()
    assert_responses()


@pytest.mark.parametrize("version", [0, 2])
def test_disabled_verification_leaves_unknowns_and_ignores_untrusted_extras(
    db, version
) -> None:
    request = _request(version)
    if version == 0:
        request = LegacyAssetRequest.model_validate(
            {**request.model_dump(exclude_unset=True), **REFERENCES}
        )
    register = register_legacy_asset if version == 0 else register_v2_asset
    register(db, request, chain_verifier=VerifiedIssuance())
    assert "issuance_txin" not in get_legacy_asset(db, request.asset_id)
    response = get_v2_asset(db, request.asset_id).model_dump()
    assert response["issuance_txin"] is None
    assert response["issuance_prevout"] is None


def test_import_persists_references_without_network_and_preserves_contract(db) -> None:
    request = _request(0)
    contract = request.contract.model_dump(exclude_unset=True)
    contract["issuance_txin"] = {"txid": "ee" * 32, "vin": 1}
    item = {"asset_id": request.asset_id, "contract": contract, **REFERENCES}
    summary = import_legacy_assets(
        db, {request.asset_id: item}, verify_imported_contract_identity=False
    )
    assert summary.imported == 1
    asset = db.scalar(select(Asset).where(Asset.asset_id == request.asset_id))
    assert asset.issuance_txid == "ab" * 32
    assert asset.contract_extra_fields["issuance_txin"] == contract["issuance_txin"]
    assert get_legacy_asset(db, request.asset_id)["contract"] == contract
    action = db.scalar(select(Action).where(Action.asset_uuid == asset.asset_uuid))
    assert action.action["request"]["contract"] == contract


def test_import_dry_run_validates_references(db) -> None:
    request = _request(0)
    item = {
        **request.model_dump(exclude_unset=True),
        "issuance_txin": {"txid": "ab" * 32, "vin": True},
    }
    summary = import_legacy_assets(
        db,
        {request.asset_id: item},
        dry_run=True,
        verify_imported_contract_identity=False,
    )
    assert summary.invalid == 1
    assert summary.would_import == 0
