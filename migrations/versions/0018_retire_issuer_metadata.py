"""Retire issuer category tags and trading venues.

Records one unsigned system action per asset whose stored values are cleared,
strips both keys from cached v2 responses, and drops the normalized tables.
Keep this independent of application models, validators, and serializers.

Revision ID: 0018_retire_issuer_metadata
Revises: 0017_reconcile_legacy_data
Create Date: 2026-11-13
"""

import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from pydantic_core import to_json
from sqlalchemy.dialects import postgresql

revision: str = "0018_retire_issuer_metadata"
down_revision: str | None = "0017_reconcile_legacy_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")
BATCH_SIZE = 500
LOCK_TIMEOUT = "30s"
OPERATION = "retire_issuer_metadata_fields"
REASON = (
    "Issuer-managed category tags and trading venues were retired; "
    "stored values were cleared."
)
RETIRED_FIELDS = ("category_tags", "trading_venues")
RETIRED_TABLES = ("asset_category_tags", "asset_trading_venues")


def upgrade() -> None:
    connection = op.get_bind()
    # Block concurrent writers between recording values and dropping the tables.
    # Fail loudly instead of hanging if a stray session holds a conflicting lock,
    # including the locks on assets and actions that dropping the foreign keys
    # needs. The explicit version bump suppresses the commit-time triggers, so
    # restoring the timeout afterward leaves no unbounded lock wait.
    previous_timeout = connection.scalar(sa.text("SHOW lock_timeout"))
    connection.execute(sa.text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    connection.execute(
        sa.text(f"LOCK TABLE {', '.join(RETIRED_TABLES)} IN ACCESS EXCLUSIVE MODE")
    )
    counts, updated_at = _record_retired_values(connection)
    counts.update(_strip_cached_fields(connection, updated_at))
    for table in RETIRED_TABLES:
        op.drop_table(table)
    _advance_registry_version(connection)
    connection.execute(
        sa.text("SELECT set_config('lock_timeout', :value, true)"),
        {"value": previous_timeout},
    )
    logger.info("Retired issuer metadata fields: %s", counts)


def downgrade() -> None:
    connection = op.get_bind()
    _create_retired_tables()
    # Cleared values stay cleared; retirement audit entries are preserved.
    counts = _restore_cached_fields(connection)
    _advance_registry_version(connection)
    logger.info("Restored retired issuer metadata fields: %s", counts)


def _record_retired_values(
    connection: sa.engine.Connection,
) -> tuple[dict[str, int], dict[Any, str]]:
    counts = {"affected_assets": 0, "removed_category_tags": 0, "removed_venues": 0}
    # Cached responses of affected assets must report the new database timestamp.
    updated_at: dict[Any, str] = {}
    after = None
    while True:
        assets = (
            connection.execute(
                sa.text("""
                SELECT a.asset_uuid, a.asset_id FROM assets a
                WHERE (CAST(:after AS uuid) IS NULL OR a.asset_uuid > CAST(:after AS uuid))
                  AND (
                    EXISTS (SELECT 1 FROM asset_category_tags t WHERE t.asset_uuid = a.asset_uuid)
                    OR EXISTS (SELECT 1 FROM asset_trading_venues v WHERE v.asset_uuid = a.asset_uuid)
                  )
                ORDER BY a.asset_uuid LIMIT :batch_size
            """),
                {"after": after, "batch_size": BATCH_SIZE},
            )
            .mappings()
            .all()
        )
        if not assets:
            break
        uuids = [row["asset_uuid"] for row in assets]
        tags: dict[Any, list[str]] = {uuid: [] for uuid in uuids}
        for row in connection.execute(
            sa.text("""
            SELECT asset_uuid, tag FROM asset_category_tags
            WHERE asset_uuid = ANY(:uuids)
            ORDER BY asset_uuid, position, created_at, category_tag_uuid
        """),
            {"uuids": uuids},
        ):
            tags[row.asset_uuid].append(row.tag)
        venues: dict[Any, list[dict[str, str]]] = {uuid: [] for uuid in uuids}
        for row in connection.execute(
            sa.text("""
            SELECT asset_uuid, name, url FROM asset_trading_venues
            WHERE asset_uuid = ANY(:uuids)
            ORDER BY asset_uuid, position, created_at, trading_venue_uuid
        """),
            {"uuids": uuids},
        ):
            venues[row.asset_uuid].append({"venue": row.name, "url": row.url})

        actions = [
            {
                "asset_uuid": str(row["asset_uuid"]),
                "action": {
                    "operation": OPERATION,
                    "asset_id": row["asset_id"],
                    "migration_revision": revision,
                    "reason": REASON,
                    "cleared_fields": list(RETIRED_FIELDS),
                    "previous_values": {
                        "category_tags": tags[row["asset_uuid"]],
                        "trading_venues": venues[row["asset_uuid"]],
                    },
                },
            }
            for row in assets
        ]
        # A null action_hash keeps the action out of the issuer hash chain.
        connection.execute(
            sa.text("""
            WITH inserted AS (
                INSERT INTO actions (asset_uuid, asset_chain_id, actor, operation, action)
                SELECT a.asset_uuid, a.asset_id, 'system', :operation, r.action
                FROM jsonb_to_recordset(CAST(:actions AS jsonb))
                    AS r(asset_uuid uuid, action jsonb)
                JOIN assets a ON a.asset_uuid = r.asset_uuid
                ORDER BY a.asset_uuid
                RETURNING action_uuid, asset_uuid
            )
            UPDATE asset_mutable_metadata m
            SET updated_at = now(), updated_by_action_uuid = inserted.action_uuid
            FROM inserted WHERE m.asset_uuid = inserted.asset_uuid
        """),
            {"operation": OPERATION, "actions": json.dumps(actions)},
        )
        for row in connection.execute(
            sa.text("""
            UPDATE assets SET updated_at = now()
            WHERE asset_uuid = ANY(:uuids)
            RETURNING asset_uuid, updated_at
        """),
            {"uuids": uuids},
        ):
            updated_at[row.asset_uuid] = _timestamp(row.updated_at)

        counts["affected_assets"] += len(assets)
        counts["removed_category_tags"] += sum(map(len, tags.values()))
        counts["removed_venues"] += sum(map(len, venues.values()))
        after = uuids[-1]
    return counts, updated_at


def _strip_cached_fields(
    connection: sa.engine.Connection, updated_at: dict[Any, str]
) -> dict[str, int]:
    def strip(payload: dict[str, Any], asset_uuid: Any) -> None:
        for field in RETIRED_FIELDS:
            payload["mutable"].pop(field, None)
        if asset_uuid in updated_at:
            payload["updated_at"] = updated_at[asset_uuid]

    return _rewrite_cached_v2(connection, strip)


def _restore_cached_fields(connection: sa.engine.Connection) -> dict[str, int]:
    def restore(payload: dict[str, Any], asset_uuid: Any) -> None:
        # Match the field order of the previous live serializer.
        payload["mutable"] = {
            "trading_venues": [],
            "category_tags": [],
            **{
                key: value
                for key, value in payload["mutable"].items()
                if key not in RETIRED_FIELDS
            },
        }

    return _rewrite_cached_v2(connection, restore)


def _rewrite_cached_v2(
    connection: sa.engine.Connection,
    rewrite: Callable[[dict[str, Any], Any], None],
) -> dict[str, int]:
    counts = {"rewritten_caches": 0, "invalidated_caches": 0}
    after = None
    while True:
        rows = (
            connection.execute(
                sa.text("""
                SELECT asset_uuid, v2_json FROM asset_serialized_fragments
                WHERE v2_json IS NOT NULL
                  AND (CAST(:after AS uuid) IS NULL OR asset_uuid > CAST(:after AS uuid))
                ORDER BY asset_uuid LIMIT :batch_size
            """),
                {"after": after, "batch_size": BATCH_SIZE},
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        updates = []
        for row in rows:
            try:
                payload = json.loads(row["v2_json"])
            except ValueError:
                payload = None
            if not isinstance(payload, dict) or not isinstance(
                payload.get("mutable"), dict
            ):
                # Readers serialize live responses when the v2 cache is missing.
                logger.warning(
                    "Invalidating undecodable v2 cache for asset %s", row["asset_uuid"]
                )
                updates.append({"uuid": row["asset_uuid"], "v2": None})
                counts["invalidated_caches"] += 1
                continue
            original = _fragment(payload)
            rewrite(payload, row["asset_uuid"])
            # Comparing text also detects key order changes.
            if (rewritten := _fragment(payload)) != original:
                updates.append({"uuid": row["asset_uuid"], "v2": rewritten})
                counts["rewritten_caches"] += 1
        if updates:
            connection.execute(
                sa.text("""
                UPDATE asset_serialized_fragments
                SET v2_json = :v2, updated_at = now()
                WHERE asset_uuid = :uuid
            """),
                updates,
            )
        after = rows[-1]["asset_uuid"]
    return counts


def _advance_registry_version(connection: sa.engine.Connection) -> None:
    # Conditional reads must not validate representations from before this change.
    # Set the flag read by bump_asset_registry_version() so the deferred
    # triggers do not bump the version a second time at commit.
    connection.execute(
        sa.text("SELECT set_config('asset_registry.version_bumped', 'on', true)")
    )
    connection.execute(
        sa.text("""
        INSERT INTO asset_registry_version (modified_at)
        VALUES (date_trunc('second', clock_timestamp()))
        ON CONFLICT (singleton) DO UPDATE SET modified_at = greatest(
            date_trunc('second', clock_timestamp()),
            asset_registry_version.modified_at + interval '1 second'
        )
    """)
    )


def _create_retired_tables() -> None:
    op.create_table(
        "asset_trading_venues",
        sa.Column(
            "trading_venue_uuid",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("asset_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_by_action_uuid", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.ForeignKeyConstraint(["asset_uuid"], ["assets.asset_uuid"]),
        sa.ForeignKeyConstraint(["updated_by_action_uuid"], ["actions.action_uuid"]),
        sa.PrimaryKeyConstraint("trading_venue_uuid"),
        sa.UniqueConstraint(
            "asset_uuid", "name", "url", name="asset_trading_venues_asset_name_url_uidx"
        ),
        sa.UniqueConstraint(
            "asset_uuid", "url", name="asset_trading_venues_asset_url_uidx"
        ),
    )
    op.create_table(
        "asset_category_tags",
        sa.Column(
            "category_tag_uuid",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("asset_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_by_action_uuid", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.ForeignKeyConstraint(["asset_uuid"], ["assets.asset_uuid"]),
        sa.ForeignKeyConstraint(["updated_by_action_uuid"], ["actions.action_uuid"]),
        sa.PrimaryKeyConstraint("category_tag_uuid"),
        sa.UniqueConstraint(
            "asset_uuid", "tag", name="asset_category_tags_asset_tag_uidx"
        ),
    )
    op.create_index(
        "asset_trading_venues_asset_position_idx",
        "asset_trading_venues",
        ["asset_uuid", "position"],
    )
    op.create_index(
        "asset_trading_venues_name_idx", "asset_trading_venues", ["name", "asset_uuid"]
    )
    op.create_index(
        "asset_category_tags_asset_position_idx",
        "asset_category_tags",
        ["asset_uuid", "position"],
    )
    op.create_index(
        "asset_category_tags_tag_idx", "asset_category_tags", ["tag", "asset_uuid"]
    )
    for table in RETIRED_TABLES:
        op.execute(
            f"""
            create constraint trigger {table}_bump_registry_version
            after insert or update or delete on {table}
            deferrable initially deferred
            for each row execute function bump_asset_registry_version()
            """
        )


def _timestamp(value: datetime) -> str:
    # Match the RFC 3339 text written by the live response serializer.
    return value.isoformat().replace("+00:00", "Z")


def _fragment(payload: dict[str, Any]) -> str:
    # Use the live fragment serializer's encoder so number formatting matches.
    return to_json(payload).decode("utf-8")
