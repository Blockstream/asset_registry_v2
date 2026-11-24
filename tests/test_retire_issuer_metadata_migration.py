import base64
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest
import wallycore as wally
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic_core import to_json
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from registry_api.canonical_json import canonical_json
from registry_api.issuer_actions import get_latest_action_hash, submit_issuer_action
from registry_api.schemas import RegisterAssetRequest
from registry_api.settings import get_settings
from registry_api.signatures import _bitcoin_signed_message_hash
from registry_api.v2_assets import get_v2_asset, register_v2_asset

PRIVATE_KEY = 1
PUBKEY = wally.ec_public_key_from_private_key(
    PRIVATE_KEY.to_bytes(wally.EC_PRIVATE_KEY_LEN, "big")
).hex()
SEEDED_AT = datetime(2026, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
migration = import_module("migrations.versions.0018_retire_issuer_metadata")


@pytest.fixture()
def connection():
    url = os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ASSET_REGISTRY_TEST_DATABASE_URL is required")
    engine = create_engine(url)
    with engine.connect() as conn:
        transaction = conn.begin()
        schema = "retire_metadata_test_" + uuid4().hex
        conn.execute(text(f"CREATE SCHEMA {schema}"))
        # Leave public off the path so unqualified names never resolve there.
        conn.execute(text(f"SET LOCAL search_path TO {schema}"))
        # Fire 0015's deferred registry-version triggers per statement; pending
        # trigger events would block later DDL in this one transaction.
        conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        yield conn
        transaction.rollback()
    engine.dispose()


@pytest.fixture()
def committed_schema():
    # Commits between steps so deferred triggers fire at commit as in production.
    # Leave public off the path so Alembic cannot find its alembic_version table.
    url = os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ASSET_REGISTRY_TEST_DATABASE_URL is required")
    schema = "retire_metadata_test_" + uuid4().hex
    schema_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(schema_url)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    try:
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def _alembic_upgrade(monkeypatch, engine, revision):
    config = Config()
    config.set_main_option("script_location", "migrations")
    try:
        with monkeypatch.context() as patch:
            patch.setenv(
                "ASSET_REGISTRY_DATABASE_URL",
                engine.url.render_as_string(hide_password=False),
            )
            get_settings.cache_clear()
            command.upgrade(config, revision)
    finally:
        get_settings.cache_clear()


def _upgrade(connection, start, end):
    with Operations.context(MigrationContext.configure(connection)):
        for path in sorted(Path("migrations/versions").glob("[0-9]*.py")):
            if start <= int(path.name[:4]) <= end:
                import_module("migrations.versions." + path.stem).upgrade()


def _downgrade(connection):
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()


def _seed(connection, *, tags=(), venues=(), asset_id=None, status="active"):
    asset_uuid = uuid4()
    asset_id = asset_id or uuid4().hex * 2
    connection.execute(
        text("""
        INSERT INTO assets (
            asset_uuid, asset_id, contract_version, domain, name, ticker, precision,
            initial_issuer_pubkey, initial_issuer_pubkey_source, current_issuer_pubkey,
            status, created_at, updated_at
        ) VALUES (
            :uuid, :asset_id, 2, :domain, 'Retire Asset', 'RET', 0,
            :pubkey, 'contract', :pubkey, :status, :seeded_at, :seeded_at
        )
    """),
        {
            "uuid": asset_uuid,
            "asset_id": asset_id,
            "domain": asset_uuid.hex + ".example.com",
            "pubkey": PUBKEY,
            "status": status,
            "seeded_at": SEEDED_AT,
        },
    )
    register_uuid = connection.scalar(
        text("""
        INSERT INTO actions (asset_uuid, asset_chain_id, actor, operation, action, action_hash)
        VALUES (:uuid, :asset_id, 'issuer', 'register', '{"operation":"register"}', :hash)
        RETURNING action_uuid
    """),
        {"uuid": asset_uuid, "asset_id": asset_id, "hash": asset_uuid.hex * 2},
    )
    connection.execute(
        text("""
        INSERT INTO asset_mutable_metadata (asset_uuid, updated_at, updated_by_action_uuid)
        VALUES (:uuid, :seeded_at, :action_uuid)
    """),
        {"uuid": asset_uuid, "seeded_at": SEEDED_AT, "action_uuid": register_uuid},
    )
    connection.execute(
        text("""
        INSERT INTO asset_custom_attributes (asset_uuid, name, value)
        VALUES (:uuid, 'category_tags', '["custom-kept"]')
    """),
        {"uuid": asset_uuid},
    )
    # Insert out of position order to verify previous values keep stored order.
    for position, tag in reversed(list(enumerate(tags))):
        connection.execute(
            text("""
            INSERT INTO asset_category_tags (asset_uuid, tag, position)
            VALUES (:uuid, :tag, :position)
        """),
            {"uuid": asset_uuid, "tag": tag, "position": position},
        )
    for position, (name, url) in reversed(list(enumerate(venues))):
        connection.execute(
            text("""
            INSERT INTO asset_trading_venues (asset_uuid, name, url, position)
            VALUES (:uuid, :name, :url, :position)
        """),
            {"uuid": asset_uuid, "name": name, "url": url, "position": position},
        )
    return asset_uuid, asset_id


def _cache(connection, asset_uuid, v2_json, legacy_json='{"legacy":true}'):
    connection.execute(
        text("""
        INSERT INTO asset_serialized_fragments (asset_uuid, legacy_json, v2_json)
        VALUES (:uuid, :legacy, :v2)
    """),
        {"uuid": asset_uuid, "legacy": legacy_json, "v2": v2_json},
    )


def _v2_cache(asset_id, *, tags=(), venues=()):
    return json.dumps(
        {
            "asset_id": asset_id,
            "mutable": {
                "trading_venues": [{"venue": n, "url": u} for n, u in venues],
                "category_tags": list(tags),
                "custom": {"category_tags": ["custom-kept"]},
            },
            "contract": {"trading_venues": "contract-kept"},
            "status": "active",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )


def _retire_actions(connection):
    return (
        connection.execute(
            text("""
            SELECT asset_uuid, actor, action, action_hash, signature, nonce,
                   audit_sequence, action_uuid
            FROM actions WHERE operation = 'retire_issuer_metadata_fields'
            ORDER BY audit_sequence
        """)
        )
        .mappings()
        .all()
    )


def _table_exists(connection, table):
    return connection.scalar(text("SELECT to_regclass(:t)"), {"t": table}) is not None


def _registry_modified_at(connection):
    return connection.scalar(text("SELECT modified_at FROM asset_registry_version"))


def test_upgrade_records_audited_clearing_and_rewrites_caches(
    connection, monkeypatch, caplog
):
    _upgrade(connection, 1, 17)
    monkeypatch.setattr(migration, "BATCH_SIZE", 2)
    caplog.set_level(logging.INFO, logger="alembic.runtime.migration")
    venues = [("sideswap", "https://sideswap.io/a"), ("bitfinex", "https://b.io/x")]
    repeated_id = uuid4().hex * 2
    tags_only = _seed(connection, tags=["tokenized", "bond"])
    venues_only = _seed(connection, venues=venues)
    both = _seed(connection, tags=["stablecoin"], venues=venues[:1])
    neither = _seed(connection)
    deregistered = _seed(
        connection, tags=["bond"], asset_id=repeated_id, status="deregistered"
    )
    reregistered = _seed(connection, venues=venues[1:], asset_id=repeated_id)
    missing_cache = _seed(connection, tags=["bond"])
    null_cache = _seed(connection, tags=["bond"])
    malformed = _seed(connection)
    no_mutable = _seed(connection)

    tags_only_cache = json.loads(_v2_cache(tags_only[1], tags=["tokenized", "bond"]))
    tags_only_cache["mutable"]["custom"]["ratio"] = 1e-07
    _cache(connection, tags_only[0], json.dumps(tags_only_cache))
    _cache(connection, venues_only[0], _v2_cache(venues_only[1], venues=venues))
    # Stale cache contents are stripped rather than trusted.
    _cache(connection, both[0], _v2_cache(both[1], tags=["stale"]))
    _cache(connection, neither[0], _v2_cache(neither[1]))
    _cache(connection, deregistered[0], _v2_cache(repeated_id, tags=["bond"]))
    _cache(connection, reregistered[0], _v2_cache(repeated_id, venues=venues[1:]))
    _cache(connection, null_cache[0], None)
    _cache(connection, malformed[0], "{not json")
    _cache(connection, no_mutable[0], '{"asset_id":"x","mutable":null}')
    before_version = _registry_modified_at(connection)
    before_heads = dict(
        connection.execute(
            text("""
            SELECT DISTINCT ON (asset_uuid) asset_uuid, action_hash FROM actions
            WHERE action_hash IS NOT NULL ORDER BY asset_uuid, audit_sequence DESC
        """)
        ).all()
    )

    _upgrade(connection, 18, 18)

    assert not _table_exists(connection, "asset_category_tags")
    assert not _table_exists(connection, "asset_trading_venues")
    affected = [tags_only, venues_only, both, deregistered, reregistered]
    affected += [missing_cache, null_cache]
    actions = _retire_actions(connection)
    assert sorted(row["asset_uuid"] for row in actions) == sorted(
        uuid for uuid, _ in affected
    )
    # Audit sequence follows UUID order across batches.
    assert [row["asset_uuid"] for row in actions] == sorted(
        row["asset_uuid"] for row in actions
    )
    by_uuid = {row["asset_uuid"]: row for row in actions}
    for row in actions:
        assert row["actor"] == "system"
        assert row["action_hash"] is None
        assert row["signature"] is None
        assert row["nonce"] is None
    assert by_uuid[tags_only[0]]["action"] == {
        "operation": "retire_issuer_metadata_fields",
        "asset_id": tags_only[1],
        "migration_revision": "0018_retire_issuer_metadata",
        "reason": migration.REASON,
        "cleared_fields": ["category_tags", "trading_venues"],
        "previous_values": {
            "category_tags": ["tokenized", "bond"],
            "trading_venues": [],
        },
    }
    assert by_uuid[venues_only[0]]["action"]["previous_values"] == {
        "category_tags": [],
        "trading_venues": [
            {"venue": "sideswap", "url": "https://sideswap.io/a"},
            {"venue": "bitfinex", "url": "https://b.io/x"},
        ],
    }
    assert by_uuid[deregistered[0]]["action"]["asset_id"] == repeated_id
    assert by_uuid[deregistered[0]]["action"]["previous_values"]["category_tags"] == [
        "bond"
    ]
    assert by_uuid[reregistered[0]]["action"]["previous_values"]["trading_venues"] == [
        {"venue": "bitfinex", "url": "https://b.io/x"}
    ]

    after_heads = dict(
        connection.execute(
            text("""
            SELECT DISTINCT ON (asset_uuid) asset_uuid, action_hash FROM actions
            WHERE action_hash IS NOT NULL ORDER BY asset_uuid, audit_sequence DESC
        """)
        ).all()
    )
    assert after_heads == before_heads
    assert (
        connection.scalar(
            text("SELECT count(*) FROM actions WHERE operation = 'register'")
        )
        == 10
    )

    rows = {
        row["asset_uuid"]: row
        for row in connection.execute(
            text("""
            SELECT a.asset_uuid, a.updated_at, m.updated_at AS metadata_updated_at,
                   m.updated_by_action_uuid, f.legacy_json, f.v2_json
            FROM assets a
            JOIN asset_mutable_metadata m ON m.asset_uuid = a.asset_uuid
            LEFT JOIN asset_serialized_fragments f ON f.asset_uuid = a.asset_uuid
        """)
        ).mappings()
    }
    for uuid, _ in affected:
        assert rows[uuid]["updated_at"] > SEEDED_AT
        assert rows[uuid]["metadata_updated_at"] == rows[uuid]["updated_at"]
        assert rows[uuid]["updated_by_action_uuid"] == by_uuid[uuid]["action_uuid"]
    for uuid, _ in (neither, malformed, no_mutable):
        assert rows[uuid]["updated_at"] == SEEDED_AT
        assert rows[uuid]["metadata_updated_at"] == SEEDED_AT

    # Rewritten numbers use the live serializer's formatting.
    assert '"ratio":1e-7' in rows[tags_only[0]]["v2_json"]
    for uuid, asset_id in (tags_only, venues_only, both, deregistered, reregistered):
        cached = json.loads(rows[uuid]["v2_json"])
        cached["mutable"]["custom"].pop("ratio", None)
        assert cached["mutable"] == {"custom": {"category_tags": ["custom-kept"]}}
        assert cached["contract"] == {"trading_venues": "contract-kept"}
        assert cached["asset_id"] == asset_id
        assert cached["updated_at"] == (
            rows[uuid]["updated_at"].isoformat().replace("+00:00", "Z")
        )
        assert rows[uuid]["legacy_json"] == '{"legacy":true}'
    unaffected = json.loads(rows[neither[0]]["v2_json"])
    assert unaffected["mutable"] == {"custom": {"category_tags": ["custom-kept"]}}
    assert unaffected["updated_at"] == "2026-01-01T00:00:00Z"
    assert rows[null_cache[0]]["v2_json"] is None
    assert rows[missing_cache[0]]["legacy_json"] is None
    for uuid, _ in (malformed, no_mutable):
        assert rows[uuid]["v2_json"] is None
        assert rows[uuid]["legacy_json"] == '{"legacy":true}'
        assert f"Invalidating undecodable v2 cache for asset {uuid}" in caplog.text

    assert _registry_modified_at(connection) > before_version
    assert (
        "'affected_assets': 7, 'removed_category_tags': 6, 'removed_venues': 4, "
        "'rewritten_caches': 6, 'invalidated_caches': 2"
    ) in caplog.text


def test_failed_upgrade_rolls_back_and_rerun_records_once(connection, monkeypatch):
    _upgrade(connection, 1, 17)
    asset_uuid, asset_id = _seed(connection, tags=["bond"])
    _cache(connection, asset_uuid, _v2_cache(asset_id, tags=["bond"]))

    def fail(_connection):
        raise RuntimeError("simulated failure")

    savepoint = connection.begin_nested()
    with monkeypatch.context() as patch:
        patch.setattr(migration, "_advance_registry_version", fail)
        with pytest.raises(RuntimeError, match="simulated failure"):
            _upgrade(connection, 18, 18)
    savepoint.rollback()

    assert _retire_actions(connection) == []
    assert connection.scalar(text("SELECT count(*) FROM asset_category_tags")) == 1
    cached = connection.scalar(text("SELECT v2_json FROM asset_serialized_fragments"))
    assert json.loads(cached)["mutable"]["category_tags"] == ["bond"]

    _upgrade(connection, 18, 18)

    assert [row["asset_uuid"] for row in _retire_actions(connection)] == [asset_uuid]


def test_downgrade_restores_empty_schema_and_reupgrade(connection):
    _upgrade(connection, 1, 17)
    asset_uuid, asset_id = _seed(
        connection, tags=["bond"], venues=[("sideswap", "https://s.io")]
    )
    _cache(connection, asset_uuid, _v2_cache(asset_id, tags=["bond"]))
    _upgrade(connection, 18, 18)
    upgraded_version = _registry_modified_at(connection)

    _downgrade(connection)

    assert connection.scalar(text("SELECT count(*) FROM asset_category_tags")) == 0
    assert connection.scalar(text("SELECT count(*) FROM asset_trading_venues")) == 0
    indexes = set(
        connection.scalars(
            text("""
            SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()
              AND tablename IN ('asset_category_tags', 'asset_trading_venues')
        """)
        )
    )
    assert {
        "asset_category_tags_asset_position_idx",
        "asset_category_tags_tag_idx",
        "asset_category_tags_asset_tag_uidx",
        "asset_trading_venues_asset_position_idx",
        "asset_trading_venues_name_idx",
        "asset_trading_venues_asset_url_uidx",
        "asset_trading_venues_asset_name_url_uidx",
    } <= indexes
    triggers = set(
        connection.scalars(
            text("""
            SELECT tgname FROM pg_trigger
            WHERE tgrelid IN (
                to_regclass('asset_category_tags'), to_regclass('asset_trading_venues')
            ) AND NOT tgisinternal
        """)
        )
    )
    assert triggers == {
        "asset_category_tags_bump_registry_version",
        "asset_trading_venues_bump_registry_version",
    }
    cached = json.loads(
        connection.scalar(text("SELECT v2_json FROM asset_serialized_fragments"))
    )
    assert list(cached["mutable"].items()) == [
        ("trading_venues", []),
        ("category_tags", []),
        ("custom", {"category_tags": ["custom-kept"]}),
    ]
    assert len(_retire_actions(connection)) == 1
    assert _registry_modified_at(connection) > upgraded_version

    _upgrade(connection, 18, 18)

    assert len(_retire_actions(connection)) == 1
    cached = json.loads(
        connection.scalar(text("SELECT v2_json FROM asset_serialized_fragments"))
    )
    assert cached["mutable"] == {"custom": {"category_tags": ["custom-kept"]}}


def test_alembic_upgrade_with_deferred_triggers_bumps_version_once(
    committed_schema, monkeypatch
):
    engine = committed_schema
    _alembic_upgrade(monkeypatch, engine, "0017_reconcile_legacy_data")
    with engine.begin() as conn:
        asset_uuid, asset_id = _seed(
            conn, tags=["bond"], venues=[("sideswap", "https://s.io")]
        )
        _cache(conn, asset_uuid, _v2_cache(asset_id, tags=["bond"]))
    # A future version makes each bump add exactly one second.
    future = datetime(2100, 1, 1, tzinfo=UTC)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE asset_registry_version SET modified_at = :at"), {"at": future}
        )

    _alembic_upgrade(monkeypatch, engine, "0018_retire_issuer_metadata")

    with engine.connect() as conn:
        assert not _table_exists(conn, "asset_category_tags")
        assert not _table_exists(conn, "asset_trading_venues")
        assert [row["asset_uuid"] for row in _retire_actions(conn)] == [asset_uuid]
        cached = json.loads(
            conn.scalar(text("SELECT v2_json FROM asset_serialized_fragments"))
        )
        assert cached["mutable"] == {"custom": {"category_tags": ["custom-kept"]}}
        assert _registry_modified_at(conn) == future + timedelta(seconds=1)


def test_upgrade_bounds_lock_wait_for_dropping_foreign_keys(
    committed_schema, monkeypatch
):
    engine = committed_schema
    _alembic_upgrade(monkeypatch, engine, "0017_reconcile_legacy_data")
    with engine.begin() as conn:
        _seed(conn, tags=["bond"])
    monkeypatch.setattr(migration, "LOCK_TIMEOUT", "1s")

    # An idle transaction that read actions blocks dropping the foreign keys.
    with engine.connect() as blocker, engine.connect() as conn:
        blocker.execute(text("SELECT 1 FROM actions LIMIT 1"))
        with (
            pytest.raises(OperationalError, match="lock timeout"),
            conn.begin(),
        ):
            _upgrade(conn, 18, 18)
        blocker.rollback()

    with engine.connect() as conn:
        assert _table_exists(conn, "asset_category_tags")
        assert _retire_actions(conn) == []


def test_fresh_install_through_full_chain(connection):
    connection.execute(text("SET LOCAL lock_timeout = '7s'"))
    _upgrade(connection, 1, 18)

    # The migration's own lock timeout must not leak into later revisions.
    assert connection.scalar(text("SHOW lock_timeout")) == "7s"

    assert not _table_exists(connection, "asset_category_tags")
    assert not _table_exists(connection, "asset_trading_venues")
    assert _retire_actions(connection) == []
    assert _registry_modified_at(connection) is not None


def test_live_responses_and_custom_actions_work_after_upgrade(connection):
    _upgrade(connection, 1, 17)
    asset_id = uuid4().hex * 2
    request = RegisterAssetRequest.model_validate(
        {
            "asset_id": asset_id,
            "contract": {
                "entity": {"domain": "retire.example.com"},
                "initial_issuer_pubkey": PUBKEY,
                "name": "Retire Asset",
                "precision": 0,
                "ticker": "RETIRE",
                "version": 2,
            },
            "mutable": {"custom": {"isin": "OLD"}},
        }
    )
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        register_v2_asset(session, request)
        head = get_latest_action_hash(session, asset_id).action_hash
    connection.execute(
        text("""
        INSERT INTO asset_category_tags (asset_uuid, tag, position)
        SELECT asset_uuid, 'bond', 0 FROM assets WHERE asset_id = :asset_id
    """),
        {"asset_id": asset_id},
    )

    _upgrade(connection, 18, 18)

    cached = connection.scalar(
        text("""
        SELECT f.v2_json FROM asset_serialized_fragments f
        JOIN assets a ON a.asset_uuid = f.asset_uuid WHERE a.asset_id = :asset_id
    """),
        {"asset_id": asset_id},
    )
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        expected = get_v2_asset(session, asset_id)
        # The migrated cache must match the live serializer byte for byte.
        assert cached == to_json(expected.model_dump(mode="json")).decode()
        assert get_latest_action_hash(session, asset_id).action_hash == head
        action = {
            "signing_context": "liquid-asset-registry-action-v1",
            "asset_id": asset_id,
            "operation": "set_custom_field",
            "prev_action_hash": head,
            "mutable_schema_version": 1,
            "timestamp": NOW.isoformat().replace("+00:00", "Z"),
            "nonce": "nonce-after-retire",
            "custom_key": "isin",
            "value": "NEW",
        }
        payload = canonical_json(action).encode("utf-8")
        signature = wally.ec_sig_from_bytes(
            PRIVATE_KEY.to_bytes(wally.EC_PRIVATE_KEY_LEN, "big"),
            _bitcoin_signed_message_hash(payload.decode("utf-8")),
            wally.EC_FLAG_ECDSA,
        )
        response = submit_issuer_action(
            session,
            asset_id=asset_id,
            payload=payload,
            signature=base64.b64encode(signature).decode(),
            now=NOW,
        )

    assert response.status == "applied"
    assert response.asset.mutable.model_dump() == {"custom": {"isin": "NEW"}}
