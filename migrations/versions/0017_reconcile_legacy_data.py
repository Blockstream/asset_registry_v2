"""Reconcile already-applied contract repairs and cached response formats.

Revision ID: 0017_reconcile_legacy_data
Revises: 0016_asset_issuance_references
Create Date: 2026-10-27
"""

import json
import logging
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from migrations.legacy_contract_repair import fragment_json, repair_legacy_contracts

revision: str = "0017_reconcile_legacy_data"
down_revision: str | None = "0016_asset_issuance_references"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")
BATCH_SIZE = 500


def upgrade() -> None:
    connection = op.get_bind()
    repair_legacy_contracts(connection)
    _reconcile_fragments(connection)


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        # Leave unparseable cached values for operator repair, like contract rows.
        logger.warning("Leaving malformed cached timestamp unchanged: %r", value)
        return value
    return parsed.isoformat().replace("+00:00", "Z")


def _reconcile_fragments(connection: sa.engine.Connection) -> None:
    after = None
    updated_count = skipped_count = 0
    while True:
        rows = (
            connection.execute(
                sa.text("""
            SELECT a.asset_uuid, a.issuance_txid, a.issuance_vin,
                   a.issuance_prevout_txid, a.issuance_prevout_vout,
                   f.legacy_json, f.v2_json, saved.request
            FROM assets a
            JOIN asset_serialized_fragments f ON f.asset_uuid = a.asset_uuid
            LEFT JOIN LATERAL (
                SELECT action->'request' AS request FROM actions
                WHERE asset_uuid = a.asset_uuid AND operation = 'legacy_register'
                ORDER BY audit_sequence LIMIT 1
            ) saved ON true
            WHERE CAST(:after AS uuid) IS NULL OR a.asset_uuid > CAST(:after AS uuid)
            ORDER BY a.asset_uuid LIMIT :batch_size
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
                references = {
                    "issuance_txin": {
                        "txid": row["issuance_txid"],
                        "vin": row["issuance_vin"],
                    }
                    if row["issuance_txid"] is not None
                    else None,
                    "issuance_prevout": {
                        "txid": row["issuance_prevout_txid"],
                        "vout": row["issuance_prevout_vout"],
                    }
                    if row["issuance_prevout_txid"] is not None
                    else None,
                }
                legacy = json.loads(row["legacy_json"])
                saved = row["request"] if isinstance(row["request"], dict) else {}
                for key, reference in references.items():
                    if reference is not None:
                        legacy[key] = reference
                    elif key in saved:
                        # Recover fields removed by the first issuance-reference implementation.
                        legacy[key] = saved[key]
                v2 = json.loads(row["v2_json"]) if row["v2_json"] is not None else None
                if v2 is not None:
                    v2.update(references)
                    for key in ("created_at", "updated_at"):
                        if isinstance(v2.get(key), str):
                            v2[key] = _timestamp(v2[key])
                    admin = v2.get("admin") or {}
                    last_action = admin.get("last_admin_action") or {}
                    if isinstance(last_action.get("timestamp"), str):
                        last_action["timestamp"] = _timestamp(last_action["timestamp"])
                legacy_text = fragment_json(legacy)
                v2_text = fragment_json(v2) if v2 is not None else None
                # Comparing text also rewrites rows saved in older spacing formats.
                if legacy_text != row["legacy_json"] or v2_text != row["v2_json"]:
                    updates.append(
                        {
                            "uuid": row["asset_uuid"],
                            "legacy": legacy_text,
                            "v2": v2_text,
                        }
                    )
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                skipped_count += 1
                # Corrupt fragment rows are reported for operator repair the same
                # way contract rows are, instead of blocking the migration.
                logger.warning(
                    "Skipping fragment reconciliation for asset %s: %s",
                    row["asset_uuid"],
                    exc,
                )
        if updates:
            connection.execute(
                sa.text("""
                UPDATE asset_serialized_fragments SET legacy_json = :legacy, v2_json = :v2
                WHERE asset_uuid = :uuid
            """),
                updates,
            )
        updated_count += len(updates)
        after = rows[-1]["asset_uuid"]
    logger.info(
        "Fragment reconciliation: updated_fragments=%s skipped=%s",
        updated_count,
        skipped_count,
    )


def downgrade() -> None:
    # Data corrections are irreversible; no schema objects were introduced.
    pass
