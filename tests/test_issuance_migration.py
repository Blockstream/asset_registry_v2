import json
import logging
import os
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from registry_api.schemas import AssetResponse
from registry_api.v2_assets import get_v2_asset

PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
TXIN = {"txid": "ab" * 32, "vin": 0}
PREVOUT = {"txid": "cd" * 32, "vout": 4294967295}
migration = import_module("migrations.versions.0015_asset_issuance_references")


@pytest.fixture()
def connection():
    url = os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ASSET_REGISTRY_TEST_DATABASE_URL is required")
    engine = create_engine(url)
    with engine.connect() as conn:
        transaction = conn.begin()
        schema = "issuance_test_" + uuid4().hex
        conn.execute(text(f"CREATE SCHEMA {schema}"))
        conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        yield conn
        transaction.rollback()
    engine.dispose()


def _upgrade(connection, start, end):
    with Operations.context(MigrationContext.configure(connection)):
        for path in sorted(Path("migrations/versions").glob("[0-9]*.py")):
            if start <= int(path.name[:4]) <= end:
                import_module("migrations.versions." + path.stem).upgrade()


def _seed(
    connection,
    *,
    request=None,
    extras=None,
    cached=None,
    asset_id=None,
    status="active",
    native=False,
):
    asset_uuid = uuid4()
    asset_id = asset_id or uuid4().hex * 2
    connection.execute(
        text("""
        INSERT INTO assets (
            asset_uuid, asset_id, contract_version, domain, name, ticker, precision,
            initial_issuer_pubkey, initial_issuer_pubkey_source, current_issuer_pubkey,
            contract_extra_fields, status, created_at, updated_at
        ) VALUES (
            :uuid, :asset_id, :version, :domain, 'Migration Asset', 'REF', 0,
            :pubkey, :source, :pubkey, CAST(:extras AS jsonb), :status,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
        )
    """),
        {
            "uuid": asset_uuid,
            "asset_id": asset_id,
            "domain": asset_uuid.hex + ".example.com",
            "version": 2 if native else 0,
            "pubkey": PUBKEY,
            "source": "contract" if native else "registry_registration",
            "extras": json.dumps(extras or {}),
            "status": status,
        },
    )
    if request is not None:
        connection.execute(
            text("""
            INSERT INTO actions (asset_uuid, asset_chain_id, actor, operation, action)
            VALUES (:uuid, :asset_id, 'system', 'legacy_register', CAST(:action AS jsonb))
        """),
            {
                "uuid": asset_uuid,
                "asset_id": asset_id,
                "action": json.dumps({"request": request}),
            },
        )
    if cached is not None:
        connection.execute(
            text("""
            INSERT INTO asset_serialized_fragments (asset_uuid, legacy_json, v2_json)
            VALUES (:uuid, :legacy, :v2)
        """),
            {
                "uuid": asset_uuid,
                "legacy": json.dumps(cached),
                "v2": json.dumps(
                    {"asset_id": asset_id, "contract": {"keep": "unchanged"}}
                ),
            },
        )
    return asset_uuid, asset_id


def test_backfill_sources_constraints_and_downgrade(connection, monkeypatch, caplog):
    _upgrade(connection, 1, 14)
    monkeypatch.setattr(migration, "BATCH_SIZE", 2)
    caplog.set_level(logging.INFO, logger="alembic.runtime.migration")
    contract = {
        "issuance_txin": {"txid": "ee" * 32, "vin": 1},
        "nested": {"keep": None},
    }
    request = {"contract": contract, "issuance_txin": TXIN, "issuance_prevout": PREVOUT}
    first, repeated_id = _seed(
        connection,
        request=request,
        extras=contract,
        cached={"contract": contract, "issuance_txin": {"txid": "ff" * 32, "vin": 3}},
        status="deregistered",
    )
    second, _ = _seed(
        connection,
        asset_id=repeated_id,
        native=True,
        cached={"contract": {"keep": "unchanged"}},
    )
    fallback, _ = _seed(
        connection,
        request={"issuance_txin": {"txid": "ab" * 32, "vin": True}},
        extras={
            "issuance_txin": {"txid": "AB" * 32, "vin": 0},
            "issuance_prevout": PREVOUT,
        },
    )
    cached, _ = _seed(
        connection, cached={"issuance_txin": TXIN, "issuance_prevout": PREVOUT}
    )
    partial, _ = _seed(
        connection,
        extras={"issuance_txin": {"txid": "11" * 32}, "issuance_prevout": PREVOUT},
    )
    # A later registration action for the same UUID must not replace its original references.
    connection.execute(
        text("""
        INSERT INTO actions (asset_uuid, asset_chain_id, actor, operation, action)
        VALUES (:uuid, :asset_id, 'system', 'legacy_register', CAST(:action AS jsonb))
    """),
        {
            "uuid": first,
            "asset_id": repeated_id,
            "action": json.dumps(
                {"request": {"issuance_txin": {"txid": "99" * 32, "vin": 1}}}
            ),
        },
    )
    actions_before = connection.execute(
        text("SELECT action_uuid, action FROM actions ORDER BY audit_sequence")
    ).all()
    _upgrade(connection, 15, 15)
    rows = {
        row["asset_uuid"]: row
        for row in connection.execute(text("SELECT * FROM assets")).mappings()
    }
    for uuid in (first, fallback, cached):
        assert rows[uuid]["issuance_txid"] == "ab" * 32
        assert rows[uuid]["issuance_vin"] == 0
        assert rows[uuid]["issuance_prevout_vout"] == 4294967295
    assert rows[second]["issuance_txid"] is None
    assert rows[second]["issuance_prevout_txid"] is None
    assert rows[partial]["issuance_txid"] is None
    assert rows[partial]["issuance_prevout_txid"] == "cd" * 32
    assert rows[first]["contract_extra_fields"] == contract
    assert all(
        row["updated_at"].isoformat() == "2026-01-01T00:00:00+00:00"
        for row in rows.values()
    )
    assert (
        connection.execute(
            text("SELECT action_uuid, action FROM actions ORDER BY audit_sequence")
        ).all()
        == actions_before
    )
    fragments = (
        connection.execute(
            text("SELECT * FROM asset_serialized_fragments WHERE asset_uuid = :uuid"),
            {"uuid": first},
        )
        .mappings()
        .one()
    )
    assert json.loads(fragments["legacy_json"])["issuance_txin"] == TXIN
    assert json.loads(fragments["legacy_json"])["contract"] == contract
    assert json.loads(fragments["v2_json"])["issuance_prevout"] == PREVOUT
    assert "conflicting_references" in caplog.text
    assert "malformed_candidates" in caplog.text
    assert "missing_references" in caplog.text

    for changes in (
        "issuance_txid = NULL",
        "issuance_vin = NULL",
        "issuance_vin = -1",
        "issuance_vin = 4294967296",
        "issuance_txid = 'bad'",
        "issuance_prevout_txid = NULL",
        "issuance_prevout_vout = NULL",
        "issuance_prevout_vout = -1",
        "issuance_prevout_vout = 4294967296",
        "issuance_prevout_txid = 'BAD'",
    ):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(f"UPDATE assets SET {changes} WHERE asset_uuid = :uuid"),
                {"uuid": first},
            )

    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
    v2 = connection.scalar(
        text("SELECT v2_json FROM asset_serialized_fragments WHERE asset_uuid = :uuid"),
        {"uuid": first},
    )
    assert "issuance_txin" not in json.loads(v2)
    legacy = connection.scalar(
        text(
            "SELECT legacy_json FROM asset_serialized_fragments WHERE asset_uuid = :uuid"
        ),
        {"uuid": first},
    )
    assert json.loads(legacy)["issuance_txin"] == TXIN
    _upgrade(connection, 15, 15)
    assert (
        connection.scalar(
            text("SELECT issuance_vin FROM assets WHERE asset_uuid = :uuid"),
            {"uuid": first},
        )
        == 0
    )


@pytest.mark.parametrize("native", [False, True])
def test_populated_historical_upgrade_keeps_cached_responses_valid(connection, native):
    _upgrade(connection, 1, 6)
    asset_uuid, asset_id = _seed(
        connection, native=native, extras={"nested": {"preserved": None}}
    )
    connection.execute(
        text("INSERT INTO asset_admin_annotations (asset_uuid) VALUES (:uuid)"),
        {"uuid": asset_uuid},
    )
    action_uuid = connection.scalar(
        text("""
        INSERT INTO actions (asset_uuid, asset_chain_id, actor, operation, action)
        VALUES (:uuid, :asset_id, 'system', 'register', '{}'::jsonb)
        RETURNING action_uuid
    """),
        {"uuid": asset_uuid, "asset_id": asset_id},
    )
    connection.execute(
        text("""
        INSERT INTO issuer_pubkey_history (asset_uuid, pubkey, valid_from_action_uuid)
        VALUES (:uuid, :pubkey, :action_uuid)
    """),
        {"uuid": asset_uuid, "pubkey": PUBKEY, "action_uuid": action_uuid},
    )
    admin_action_uuid = connection.scalar(
        text("""
        INSERT INTO actions (asset_uuid, asset_chain_id, actor, operation, action, admin_id)
        VALUES (:uuid, :asset_id, 'admin', 'update_admin_annotations', '{"field":"featured"}'::jsonb, 'reviewer')
        RETURNING action_uuid
    """),
        {"uuid": asset_uuid, "asset_id": asset_id},
    )
    connection.execute(
        text("""
        UPDATE asset_admin_annotations SET featured = true, last_admin_action_uuid = :action_uuid
        WHERE asset_uuid = :uuid
    """),
        {"uuid": asset_uuid, "action_uuid": admin_action_uuid},
    )
    connection.execute(
        text(
            "INSERT INTO asset_trading_venues (asset_uuid, name, url, position) VALUES (:uuid, 'sideswap', 'https://sideswap.io', 0)"
        ),
        {"uuid": asset_uuid},
    )
    connection.execute(
        text(
            "INSERT INTO asset_category_tags (asset_uuid, tag, position) VALUES (:uuid, 'bond', 0)"
        ),
        {"uuid": asset_uuid},
    )
    connection.execute(
        text(
            "INSERT INTO asset_custom_attributes (asset_uuid, name, value) VALUES (:uuid, 'keep', 'null'::jsonb)"
        ),
        {"uuid": asset_uuid},
    )
    _upgrade(connection, 7, 15)
    v2 = connection.scalar(
        text("SELECT v2_json FROM asset_serialized_fragments WHERE asset_uuid = :uuid"),
        {"uuid": asset_uuid},
    )
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        expected = get_v2_asset(session, asset_id)
    assert AssetResponse.model_validate_json(v2).model_dump() == expected.model_dump()


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {"txid": "bad", "vin": 0},
        {"txid": "ab" * 32, "vin": True},
        {"txid": "ab" * 32, "vin": 1e50},
        {"txid": "ab" * 32, "vin": "0"},
    ],
)
def test_frozen_backfill_validator_rejects_malformed_values(value):
    assert migration._reference(value, "vin") is None
