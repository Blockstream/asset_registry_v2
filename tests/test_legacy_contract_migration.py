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
from registry_api.constants import Actor, Operation
from registry_api.models import Action, Asset, AssetSerializedFragment

PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
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
    null_asset = _asset("aa" * 32, "null.example.com", "NULL")
    real_asset = _asset("bb" * 32, "real.example.com", "REAL")
    with Session(engine) as session:
        session.add_all([null_asset, real_asset])
        session.flush()
        null_asset_uuid = null_asset.asset_uuid
        registered_contract = {
            "name": null_asset.name,
            "nested": {"preserved_null": None},
        }
        session.add(
            new_action(
                null_asset,
                actor=Actor.SYSTEM,
                operation=Operation.LEGACY_REGISTER,
                payload={
                    "request": {
                        "contract": {
                            "collection": None,
                            **registered_contract,
                            "ticker": None,
                        }
                    },
                    "contract_hash": contract_hash(registered_contract),
                },
            )
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

    null_extras, null_legacy, null_v2 = rows[0][1:]
    real_extras, real_legacy, real_v2 = rows[1][1:]
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
