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
            "issuance_txin": {"txid": "ee" * 32, "vin": 1},
            "issuance_prevout": {"txid": "ff" * 32, "vout": 2},
        },
        cached={
            "issuance_txin": {"txid": "AB" * 32, "vin": 0},
            "issuance_prevout": PREVOUT,
        },
    )
    cached, _ = _seed(
        connection, cached={"issuance_txin": TXIN, "issuance_prevout": PREVOUT}
    )
    partial, _ = _seed(
        connection,
        request={"issuance_prevout": PREVOUT},
        extras={"issuance_txin": TXIN},
    )
    extras_only, _ = _seed(
        connection,
        extras={"issuance_txin": TXIN, "issuance_prevout": PREVOUT},
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
    assert rows[extras_only]["issuance_txid"] is None
    assert rows[extras_only]["issuance_prevout_txid"] is None
    assert rows[extras_only]["contract_extra_fields"] == {
        "issuance_txin": TXIN,
        "issuance_prevout": PREVOUT,
    }
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
    _upgrade(connection, 7, 16)
    v2 = connection.scalar(
        text("SELECT v2_json FROM asset_serialized_fragments WHERE asset_uuid = :uuid"),
        {"uuid": asset_uuid},
    )
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        expected = get_v2_asset(session, asset_id)
    assert AssetResponse.model_validate_json(v2).model_dump() == expected.model_dump()
    cached = json.loads(v2)
    serialized = expected.model_dump(mode="json")
    for key in ("created_at", "updated_at"):
        assert cached[key] == serialized[key]
    assert (
        cached["admin"]["last_admin_action"]["timestamp"]
        == serialized["admin"]["last_admin_action"]["timestamp"]
    )


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


@pytest.mark.parametrize("start", [14, 15])
def test_forward_repair_recovers_old_migration_results(connection, start, caplog):
    from registry_api.canonical_json import contract_hash
    from registry_api.chain import derive_asset_id

    _upgrade(connection, 1, start)
    # Original 0014 erased collection from the action and projections, but the
    # top-level fallback and issuance identity still prove that it was genuine.
    registered = {"name": "Genuine collection null", "collection": None}
    stripped = {"name": registered["name"]}
    asset_id = derive_asset_id(
        PREVOUT["txid"], PREVOUT["vout"], contract_hash(registered)
    )
    malformed_txin = {"txid": "ab" * 32, "vin": "0", "future": True}
    uuid, _ = _seed(
        connection,
        asset_id=asset_id,
        request={
            "contract": stripped,
            "collection": None,
            "issuance_prevout": PREVOUT,
            "issuance_txin": malformed_txin,
        },
        cached={"contract": stripped},
    )
    connection.execute(
        text("""
        UPDATE actions SET action = action || jsonb_build_object('contract_hash', CAST(:hash AS text))
        WHERE asset_uuid = :uuid
    """),
        {"uuid": uuid, "hash": contract_hash(stripped)},
    )
    connection.execute(
        text("""
        UPDATE asset_serialized_fragments
        SET v2_json = '{"contract":{},"created_at":"2026-01-01T00:00:00+00:00",
                       "updated_at":"2026-01-01T00:00:00.120000+00:00",
                       "admin":{"last_admin_action":{"timestamp":"2026-01-01T00:00:00+00:00"}}}'
        WHERE asset_uuid = :uuid
    """),
        {"uuid": uuid},
    )
    if start == 15:
        # Simulate the first 0015 dropping the malformed legacy field.
        connection.execute(
            text(
                "UPDATE assets SET issuance_prevout_txid = :txid, issuance_prevout_vout = :vout WHERE asset_uuid = :uuid"
            ),
            {"uuid": uuid, **PREVOUT},
        )

    bad_uuid, bad_id = _seed(
        connection,
        request={
            "contract": {"name": "Unresolvable", "unknown_null": None},
            "issuance_prevout": {"txid": "bad", "vout": "0"},
        },
        extras={"unknown_null": None},
        cached={"contract": {"unknown_null": None}},
    )
    bad_before = connection.scalar(
        text("SELECT action FROM actions WHERE asset_uuid = :uuid"), {"uuid": bad_uuid}
    )
    caplog.set_level(logging.WARNING, logger="alembic.runtime.migration")
    _upgrade(connection, start + 1, 16)
    action = connection.scalar(
        text("SELECT action FROM actions WHERE asset_uuid = :uuid"), {"uuid": uuid}
    )
    assert action["request"]["contract"] == registered
    assert action["contract_hash"] == contract_hash(registered)
    assert connection.scalar(
        text("SELECT contract_extra_fields FROM assets WHERE asset_uuid = :uuid"),
        {"uuid": uuid},
    ) == {"collection": None}
    fragment = (
        connection.execute(
            text("SELECT * FROM asset_serialized_fragments WHERE asset_uuid = :uuid"),
            {"uuid": uuid},
        )
        .mappings()
        .one()
    )
    legacy, v2 = json.loads(fragment["legacy_json"]), json.loads(fragment["v2_json"])
    assert legacy["contract"] == registered
    assert legacy["issuance_txin"] == malformed_txin
    assert v2["issuance_txin"] is None
    assert v2["issuance_prevout"] == PREVOUT
    assert v2["created_at"] == "2026-01-01T00:00:00Z"
    assert v2["updated_at"] == "2026-01-01T00:00:00.120000Z"
    assert v2["admin"]["last_admin_action"]["timestamp"] == "2026-01-01T00:00:00Z"
    assert (
        connection.scalar(
            text("SELECT action FROM actions WHERE asset_uuid = :uuid"),
            {"uuid": bad_uuid},
        )
        == bad_before
    )
    assert bad_id in caplog.text
    # Repeating the repair is safe and never restores synthesized fields.
    _upgrade(connection, 16, 16)
    assert (
        connection.scalar(
            text("SELECT action FROM actions WHERE asset_uuid = :uuid"), {"uuid": uuid}
        )
        == action
    )


def test_forward_repair_fixes_uncompressed_issuer_after_old_0014(connection):
    from migrations.legacy_contract_repair import (
        TESTNET_LEGACY_UNCOMPRESSED_ISSUER_PUBKEYS,
    )
    from registry_api.canonical_json import contract_hash

    _upgrade(connection, 1, 15)
    asset_id = next(iter(TESTNET_LEGACY_UNCOMPRESSED_ISSUER_PUBKEYS))
    item = json.loads(Path("scripts/data/testnet_assets_9f806b4.json").read_text())[
        asset_id
    ]
    original = item["contract"]
    stored = {**original, "issuer_pubkey": "03" + original["issuer_pubkey"][2:66]}
    uuid, _ = _seed(
        connection,
        asset_id=asset_id,
        request={**item, "contract": stored},
        cached={"contract": stored},
    )
    connection.execute(
        text(
            "UPDATE actions SET action = action || jsonb_build_object('contract_hash', CAST(:hash AS text)) WHERE asset_uuid = :uuid"
        ),
        {"uuid": uuid, "hash": contract_hash(stored)},
    )
    _upgrade(connection, 16, 16)
    action = connection.scalar(
        text("SELECT action FROM actions WHERE asset_uuid = :uuid"), {"uuid": uuid}
    )
    assert action["request"]["contract"] == original
    assert action["contract_hash"] == contract_hash(original)


def test_forward_repair_restores_native_null_without_modifying_signed_action(
    connection,
):
    _upgrade(connection, 1, 15)
    uuid, asset_id = _seed(
        connection, native=True, cached={"contract": {"name": "Native"}}
    )
    action = {"contract": {"name": "Native", "extension_null": None}}
    connection.execute(
        text("""
        INSERT INTO actions (asset_uuid, asset_chain_id, actor, operation, action, action_hash)
        VALUES (:uuid, :asset_id, 'system', 'register', CAST(:action AS jsonb), :hash)
    """),
        {
            "uuid": uuid,
            "asset_id": asset_id,
            "action": json.dumps(action),
            "hash": "aa" * 32,
        },
    )
    _upgrade(connection, 16, 16)
    assert (
        connection.scalar(
            text("SELECT action FROM actions WHERE asset_uuid = :uuid"), {"uuid": uuid}
        )
        == action
    )
    assert (
        connection.scalar(
            text("SELECT action_hash FROM actions WHERE asset_uuid = :uuid"),
            {"uuid": uuid},
        )
        == "aa" * 32
    )
    assert connection.scalar(
        text("SELECT contract_extra_fields FROM assets WHERE asset_uuid = :uuid"),
        {"uuid": uuid},
    ) == {"extension_null": None}
    for column in ("legacy_json", "v2_json"):
        cached = connection.scalar(
            text(
                f"SELECT {column} FROM asset_serialized_fragments WHERE asset_uuid = :uuid"
            ),
            {"uuid": uuid},
        )
        assert json.loads(cached)["contract"]["extension_null"] is None


def test_backfill_keeps_untyped_legacy_cache_values(connection):
    _upgrade(connection, 1, 14)
    raw = {"txid": "bad", "vin": "0"}
    uuid, _ = _seed(connection, cached={"issuance_txin": raw})
    _upgrade(connection, 15, 16)
    assert (
        connection.scalar(
            text("SELECT issuance_txid FROM assets WHERE asset_uuid = :uuid"),
            {"uuid": uuid},
        )
        is None
    )
    cached = connection.scalar(
        text(
            "SELECT legacy_json FROM asset_serialized_fragments WHERE asset_uuid = :uuid"
        ),
        {"uuid": uuid},
    )
    assert json.loads(cached)["issuance_txin"] == raw


def test_reconcile_writes_compact_fragments_and_skips_unchanged_rows(connection):
    _upgrade(connection, 1, 15)
    uuid, _ = _seed(connection, cached={"name": "Café", "issuance_txin": None})
    reconcile = import_module("migrations.versions.0016_reconcile_legacy_data")
    reconcile._reconcile_fragments(connection)
    query = text(
        "SELECT ctid::text AS ctid, legacy_json, v2_json "
        "FROM asset_serialized_fragments WHERE asset_uuid = :uuid"
    )
    first = connection.execute(query, {"uuid": uuid}).mappings().one()
    assert first["legacy_json"] == '{"name":"Café","issuance_txin":null}'
    assert ": " not in first["v2_json"] and ", " not in first["v2_json"]

    reconcile._reconcile_fragments(connection)
    assert connection.execute(query, {"uuid": uuid}).mappings().one() == first
