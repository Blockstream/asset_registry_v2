"""Persist original issuance references without changing registered contracts.

Revision ID: 0015_asset_issuance_references
Revises: 0014_preserve_legacy_contract
Create Date: 2026-10-20
"""

import json
import logging
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0015_asset_issuance_references"
down_revision: str | None = "0014_preserve_legacy_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")
BATCH_SIZE = 500


def upgrade() -> None:
    for column, type_ in (
        ("issuance_txid", sa.String(64)),
        ("issuance_vin", sa.BigInteger()),
        ("issuance_prevout_txid", sa.String(64)),
        ("issuance_prevout_vout", sa.BigInteger()),
    ):
        op.add_column("assets", sa.Column(column, type_, nullable=True))
    op.create_check_constraint(
        "assets_issuance_txin_chk",
        "assets",
        "(issuance_txid IS NULL) = (issuance_vin IS NULL) AND "
        "issuance_txid ~ '^[0-9a-f]{64}$' AND issuance_vin BETWEEN 0 AND 4294967295",
    )
    op.create_check_constraint(
        "assets_issuance_prevout_chk",
        "assets",
        "(issuance_prevout_txid IS NULL) = (issuance_prevout_vout IS NULL) AND "
        "issuance_prevout_txid ~ '^[0-9a-f]{64}$' AND issuance_prevout_vout BETWEEN 0 AND 4294967295",
    )
    _backfill()


def _object(value: Any) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _reference(value: Any, index: str) -> dict | None:
    if not isinstance(value, dict):
        return None
    txid, number = value.get("txid"), value.get(index)
    if (
        not isinstance(txid, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", txid) is None
        or type(number) is not int
        or not 0 <= number <= 4294967295
    ):
        return None
    return {"txid": txid.lower(), index: number}


def _backfill() -> None:
    # Freeze the migration's data format here: importing current ORM models or
    # serializers would make historical upgrades depend on future columns.
    bind = op.get_bind()
    counts: Counter = Counter()
    after = None
    while True:
        rows = (
            bind.execute(
                sa.text("""
            SELECT a.asset_uuid, a.contract_extra_fields, saved.request,
                   f.legacy_json, f.v2_json
            FROM assets AS a
            LEFT JOIN LATERAL (
                SELECT action->'request' AS request FROM actions
                WHERE asset_uuid = a.asset_uuid AND operation = 'legacy_register'
                ORDER BY audit_sequence ASC LIMIT 1
            ) AS saved ON true
            LEFT JOIN asset_serialized_fragments AS f ON f.asset_uuid = a.asset_uuid
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
        assets, fragments = [], []
        for row in rows:
            counts["assets"] += 1
            legacy = _object(row["legacy_json"])
            sources = (
                _object(row["request"]),
                _object(row["contract_extra_fields"]),
                legacy,
            )
            references = {}
            for key, index in (("issuance_txin", "vin"), ("issuance_prevout", "vout")):
                candidates = []
                for source in sources:
                    value = source.get(key)
                    candidate = _reference(value, index)
                    if candidate is not None:
                        candidates.append(candidate)
                    elif value is not None:
                        counts["malformed_candidates"] += 1
                selected = candidates[0] if candidates else None
                references[key] = selected
                counts[
                    "backfilled_references" if selected else "missing_references"
                ] += 1
                if any(candidate != selected for candidate in candidates[1:]):
                    counts["conflicting_references"] += 1
            txin, prevout = references["issuance_txin"], references["issuance_prevout"]
            assets.append(
                {
                    "asset_uuid": row["asset_uuid"],
                    "txid": txin["txid"] if txin else None,
                    "vin": txin["vin"] if txin else None,
                    "prevout_txid": prevout["txid"] if prevout else None,
                    "vout": prevout["vout"] if prevout else None,
                }
            )
            if row["legacy_json"] is not None:
                legacy.pop("issuance_txin", None)
                legacy.pop("issuance_prevout", None)
                legacy.update(
                    {
                        key: value
                        for key, value in references.items()
                        if value is not None
                    }
                )
                v2 = _object(row["v2_json"]) if row["v2_json"] is not None else None
                if v2 is not None:
                    v2.update(references)
                fragments.append(
                    {
                        "asset_uuid": row["asset_uuid"],
                        "legacy_json": json.dumps(legacy),
                        "v2_json": json.dumps(v2) if v2 is not None else None,
                    }
                )
        bind.execute(
            sa.text("""
            UPDATE assets SET issuance_txid = :txid, issuance_vin = :vin,
                issuance_prevout_txid = :prevout_txid, issuance_prevout_vout = :vout
            WHERE asset_uuid = :asset_uuid
        """),
            assets,
        )
        if fragments:
            bind.execute(
                sa.text("""
                UPDATE asset_serialized_fragments
                SET legacy_json = :legacy_json, v2_json = :v2_json
                WHERE asset_uuid = :asset_uuid
            """),
                fragments,
            )
        after = rows[-1]["asset_uuid"]
    logger.info("Issuance reference backfill: %s", dict(counts))


def downgrade() -> None:
    # Legacy readers can still use the saved references. Older v2 readers do
    # not declare these fields, so remove them from the cached v2 projection.
    op.execute("""
        UPDATE asset_serialized_fragments
        SET v2_json = (v2_json::jsonb - 'issuance_txin' - 'issuance_prevout')::text
        WHERE v2_json IS NOT NULL
    """)
    op.drop_constraint("assets_issuance_txin_chk", "assets", type_="check")
    op.drop_constraint("assets_issuance_prevout_chk", "assets", type_="check")
    for column in (
        "issuance_txid",
        "issuance_vin",
        "issuance_prevout_txid",
        "issuance_prevout_vout",
    ):
        op.drop_column("assets", column)
