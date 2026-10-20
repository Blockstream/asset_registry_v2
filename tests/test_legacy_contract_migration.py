import json
import os
from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from registry_api.action_writer import new_action
from registry_api.canonical_json import contract_hash
from registry_api.chain import derive_asset_id
from registry_api.constants import Actor, Operation
from registry_api.models import Action, Asset, AssetSerializedFragment

PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
COMPRESSED_TESTNET_PUBKEY = (
    "03d8bb0680c89a19042a4c30f52ebb334dc075bcd53191e4de3885faa0eb6437db"
)
migration = import_module("migrations.versions.0014_preserve_legacy_contract")


pytestmark = pytest.mark.skipif(
    not os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL"),
    reason="ASSET_REGISTRY_TEST_DATABASE_URL is required for migration tests",
)


@pytest.fixture()
def engine():
    database_engine = create_engine(os.environ["ASSET_REGISTRY_TEST_DATABASE_URL"])
    with database_engine.begin() as connection:
        connection.execute(text("delete from actions"))
        connection.execute(text("delete from assets"))
    try:
        yield database_engine
    finally:
        with database_engine.begin() as connection:
            connection.execute(text("delete from actions"))
            connection.execute(text("delete from assets"))
        database_engine.dispose()


def test_migration_removes_only_synthesized_null_contract_collection(engine) -> None:
    registered_contract = {
        "name": "NULL asset",
        "nested": {"preserved_null": None},
    }
    prevout_txid = "10" * 32
    prevout_vout = 0
    null_asset_id = derive_asset_id(
        prevout_txid,
        prevout_vout,
        contract_hash(registered_contract),
    )
    null_asset = _asset(null_asset_id, "null.example.com", "NULL")
    real_asset_id = "bb" * 32
    genuine_null_asset_id = "cc" * 32
    real_asset = _asset(real_asset_id, "real.example.com", "REAL")
    genuine_null_asset = _asset(genuine_null_asset_id, "genuine.example.com", "GENUINE")
    genuine_null_asset.contract_extra_fields = {
        "custom_null": None,
        "synthesized_extra": None,
    }
    with Session(engine) as session:
        session.add_all([null_asset, real_asset, genuine_null_asset])
        session.flush()
        null_asset_uuid = null_asset.asset_uuid
        genuine_null_asset_uuid = genuine_null_asset.asset_uuid
        genuine_null_contract = {
            "custom_null": None,
            "name": genuine_null_asset.name,
        }
        session.add_all(
            [
                new_action(
                    null_asset,
                    actor=Actor.SYSTEM,
                    operation=Operation.LEGACY_REGISTER,
                    payload={
                        "request": {
                            "asset_id": null_asset.asset_id,
                            "contract": {
                                "collection": None,
                                **registered_contract,
                                "ticker": None,
                            },
                            "issuance_prevout": {
                                "txid": prevout_txid,
                                "vout": prevout_vout,
                            },
                        },
                        "contract_hash": contract_hash(registered_contract),
                    },
                ),
                new_action(
                    genuine_null_asset,
                    actor=Actor.SYSTEM,
                    operation=Operation.LEGACY_REGISTER,
                    payload={
                        "request": {"contract": genuine_null_contract},
                        "contract_hash": contract_hash(genuine_null_contract),
                    },
                ),
            ]
        )
        session.add_all(
            [
                AssetSerializedFragment(
                    asset_uuid=null_asset.asset_uuid,
                    legacy_json=json.dumps(
                        {
                            "asset_id": null_asset.asset_id,
                            "collection": None,
                            "ticker": None,
                            "contract": {
                                "collection": None,
                                "name": null_asset.name,
                                "nested": {"preserved_null": None},
                                "ticker": None,
                            },
                        }
                    ),
                    v2_json=json.dumps(
                        {
                            "asset_id": null_asset.asset_id,
                            "contract": {
                                "collection": None,
                                "name": null_asset.name,
                                "nested": {"preserved_null": None},
                                "ticker": None,
                            },
                        }
                    ),
                ),
                AssetSerializedFragment(
                    asset_uuid=real_asset.asset_uuid,
                    legacy_json=json.dumps(
                        {
                            "asset_id": real_asset.asset_id,
                            "collection": "Real collection",
                            "contract": {
                                "collection": "Real collection",
                                "name": real_asset.name,
                            },
                        }
                    ),
                    v2_json=json.dumps(
                        {
                            "asset_id": real_asset.asset_id,
                            "contract": {
                                "collection": "Real collection",
                                "name": real_asset.name,
                            },
                        }
                    ),
                ),
                AssetSerializedFragment(
                    asset_uuid=genuine_null_asset.asset_uuid,
                    legacy_json=json.dumps(
                        {
                            "asset_id": genuine_null_asset.asset_id,
                            "contract": {
                                **genuine_null_contract,
                                "ticker": None,
                            },
                        }
                    ),
                    v2_json=json.dumps(
                        {
                            "asset_id": genuine_null_asset.asset_id,
                            "contract": {
                                **genuine_null_contract,
                                "ticker": None,
                            },
                        }
                    ),
                ),
            ]
        )
        session.commit()

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

    with Session(engine) as session:
        rows = session.execute(
            select(
                Asset.asset_id,
                Asset.contract_extra_fields,
                AssetSerializedFragment.legacy_json,
                AssetSerializedFragment.v2_json,
            )
            .join(
                AssetSerializedFragment,
                AssetSerializedFragment.asset_uuid == Asset.asset_uuid,
            )
            .order_by(Asset.asset_id)
        ).all()
        action_payload = session.scalar(
            select(Action.action).where(Action.asset_uuid == null_asset_uuid)
        )
        genuine_action_payload = session.scalar(
            select(Action.action).where(Action.asset_uuid == genuine_null_asset_uuid)
        )

    rows_by_asset_id = {row[0]: row[1:] for row in rows}
    null_extras, null_legacy, null_v2 = rows_by_asset_id[null_asset_id]
    real_extras, real_legacy, real_v2 = rows_by_asset_id[real_asset_id]
    genuine_extras, genuine_legacy, genuine_v2 = rows_by_asset_id[genuine_null_asset_id]
    assert null_extras == {"nested": {"preserved_null": None}}
    assert json.loads(null_legacy)["collection"] is None
    assert json.loads(null_legacy)["ticker"] is None
    assert "collection" not in json.loads(null_legacy)["contract"]
    assert "ticker" not in json.loads(null_legacy)["contract"]
    assert json.loads(null_legacy)["contract"]["nested"] == {"preserved_null": None}
    assert "collection" not in json.loads(null_v2)["contract"]
    assert "ticker" not in json.loads(null_v2)["contract"]
    assert action_payload is not None
    assert action_payload["request"]["contract"] == registered_contract
    assert action_payload["contract_hash"] == contract_hash(
        action_payload["request"]["contract"]
    )
    assert real_extras == {"collection": "Real collection"}
    assert json.loads(real_legacy)["contract"]["collection"] == "Real collection"
    assert json.loads(real_v2)["contract"]["collection"] == "Real collection"
    assert genuine_extras == {"custom_null": None}
    assert genuine_action_payload is not None
    assert genuine_action_payload["request"]["contract"] == genuine_null_contract
    assert json.loads(genuine_legacy)["contract"]["custom_null"] is None
    assert "ticker" not in json.loads(genuine_legacy)["contract"]
    assert json.loads(genuine_v2)["contract"]["custom_null"] is None
    assert "ticker" not in json.loads(genuine_v2)["contract"]


def test_migration_prefers_issuance_identity_over_conflicting_stored_hash(
    engine,
) -> None:
    current_contract = {"custom_null": None, "name": "Genuine null"}
    stripped_contract = {"name": "Genuine null"}
    prevout_txid = "11" * 32
    prevout_vout = 1
    asset_id = derive_asset_id(
        prevout_txid,
        prevout_vout,
        contract_hash(current_contract),
    )
    asset = _asset(asset_id, "conflict.example.com", "CONFLICT")
    with Session(engine) as session:
        session.add(asset)
        session.flush()
        session.add(
            new_action(
                asset,
                actor=Actor.SYSTEM,
                operation=Operation.LEGACY_REGISTER,
                payload={
                    "request": {
                        "asset_id": asset_id,
                        "contract": current_contract,
                        "issuance_prevout": {
                            "txid": prevout_txid,
                            "vout": prevout_vout,
                        },
                    },
                    "contract_hash": contract_hash(stripped_contract),
                },
            )
        )
        session.commit()

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

    with Session(engine) as session:
        action_payload = session.scalar(select(Action.action))

    assert action_payload is not None
    assert action_payload["request"]["contract"] == current_contract
    assert action_payload["contract_hash"] == contract_hash(current_contract)


def test_migration_repairs_mixed_genuine_and_synthesized_root_nulls(engine) -> None:
    registered_contract = {"custom_null": None, "name": "Mixed nulls"}
    stored_contract = {
        **registered_contract,
        "collection": None,
    }
    stripped_contract = {"name": "Mixed nulls"}
    prevout = {"txid": "12" * 32, "vout": 0}
    asset_id = derive_asset_id(
        prevout["txid"],
        prevout["vout"],
        contract_hash(registered_contract),
    )
    asset = _asset(asset_id, "mixed.example.com", "MIXED")
    with Session(engine) as session:
        session.add(asset)
        session.flush()
        session.add(
            new_action(
                asset,
                actor=Actor.SYSTEM,
                operation=Operation.LEGACY_REGISTER,
                payload={
                    "request": {
                        "asset_id": asset.asset_id,
                        "contract": stored_contract,
                        "issuance_prevout": prevout,
                    },
                    "contract_hash": contract_hash(stripped_contract),
                },
            )
        )
        session.commit()

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

    with Session(engine) as session:
        payload = session.scalar(select(Action.action))
    assert payload["request"]["contract"] == registered_contract
    assert payload["contract_hash"] == contract_hash(registered_contract)


def test_migration_restores_original_uncompressed_testnet_contract(engine) -> None:
    asset_id = "92ac106ea13e65fe1ccae10ca69d6bd14147bb445f4c6119b8db5d027c543d15"
    prevout = {
        "txid": "2af4bc4eaee616d8fd8ae0fdc501647eb5ed8edb251bb4fe7760e55dd3ab823e",
        "vout": 1,
    }
    original_contract = {
        "entity": {"domain": "api.dev.iodevnet.com"},
        "issuer_pubkey": migration.TESTNET_LEGACY_UNCOMPRESSED_ISSUER_PUBKEYS[asset_id],
        "name": "BrianCoin",
        "precision": 8,
        "ticker": "BRI",
        "version": 0,
    }
    stored_contract = {
        **original_contract,
        "collection": None,
        "issuer_pubkey": COMPRESSED_TESTNET_PUBKEY,
    }
    stored_hashed_contract = {
        key: value for key, value in stored_contract.items() if value is not None
    }
    assert (
        derive_asset_id(
            prevout["txid"], prevout["vout"], contract_hash(original_contract)
        )
        == asset_id
    )

    asset = _asset(asset_id, "api.dev.iodevnet.com", "BRI")
    asset.contract_extra_fields = {"collection": None}
    asset.initial_issuer_pubkey = COMPRESSED_TESTNET_PUBKEY
    asset.current_issuer_pubkey = COMPRESSED_TESTNET_PUBKEY
    with Session(engine) as session:
        session.add(asset)
        session.flush()
        asset_uuid = asset.asset_uuid
        session.add(
            new_action(
                asset,
                actor=Actor.SYSTEM,
                operation=Operation.LEGACY_REGISTER,
                payload={
                    "request": {
                        "asset_id": asset_id,
                        "contract": stored_contract,
                        "issuance_prevout": prevout,
                    },
                    "contract_hash": contract_hash(stored_hashed_contract),
                },
            )
        )
        session.add(
            AssetSerializedFragment(
                asset_uuid=asset_uuid,
                legacy_json=json.dumps(
                    {"contract": {**original_contract, "collection": None}}
                ),
                v2_json=json.dumps({"contract": stored_contract}),
            )
        )
        session.commit()

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

    with Session(engine) as session:
        action_payload = session.scalar(select(Action.action))
        fragment = session.get(AssetSerializedFragment, asset_uuid)
        extras = session.scalar(
            select(Asset.contract_extra_fields).where(Asset.asset_uuid == asset_uuid)
        )

    assert action_payload is not None
    assert action_payload["request"]["contract"] == original_contract
    assert action_payload["contract_hash"] == contract_hash(original_contract)
    assert extras == {}
    assert fragment is not None
    assert json.loads(fragment.legacy_json)["contract"] == original_contract
    assert "collection" not in json.loads(fragment.v2_json)["contract"]


def test_migration_uses_v2_registration_contract_for_null_provenance(engine) -> None:
    asset = _asset("dd" * 32, "v2.example.com", "V2")
    asset.contract_version = 2
    asset.initial_issuer_pubkey_source = "contract"
    registered_contract = {
        "entity": {"domain": asset.domain},
        "extension_null": None,
        "initial_issuer_pubkey": PUBKEY,
        "name": asset.name,
        "precision": asset.precision,
        "ticker": asset.ticker,
        "version": asset.contract_version,
    }
    projected_contract = {
        **registered_contract,
        "issuer_pubkey": None,
    }
    with Session(engine) as session:
        session.add(asset)
        session.flush()
        asset_uuid = asset.asset_uuid
        session.add(
            new_action(
                asset,
                actor=Actor.SYSTEM,
                operation=Operation.REGISTER,
                payload={
                    "contract": registered_contract,
                    "contract_hash": contract_hash(registered_contract),
                },
                participates_in_hash_chain=True,
            )
        )
        session.add(
            AssetSerializedFragment(
                asset_uuid=asset_uuid,
                legacy_json=json.dumps({"contract": projected_contract}),
                v2_json=json.dumps({"contract": projected_contract}),
            )
        )
        session.commit()

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

    with Session(engine) as session:
        fragment = session.get(AssetSerializedFragment, asset_uuid)

    assert fragment is not None
    for payload in (json.loads(fragment.legacy_json), json.loads(fragment.v2_json)):
        assert payload["contract"]["extension_null"] is None
        assert "issuer_pubkey" not in payload["contract"]


def _asset(asset_id: str, domain: str, ticker: str) -> Asset:
    return Asset(
        asset_id=asset_id,
        contract_version=0,
        domain=domain,
        name=f"{ticker} asset",
        ticker=ticker,
        precision=0,
        contract_extra_fields=(
            {
                "collection": None,
                "nested": {"preserved_null": None},
                "root_null": None,
            }
            if ticker == "NULL"
            else {"collection": "Real collection"}
        ),
        domain_verification_method="http",
        initial_issuer_pubkey=PUBKEY,
        initial_issuer_pubkey_source="registry_registration",
        current_issuer_pubkey=PUBKEY,
        mutable_schema_version=1,
        status="active",
    )
