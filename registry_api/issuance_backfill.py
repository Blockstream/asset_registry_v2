"""Explicit, retryable chain backfill for references absent from local history."""

import argparse
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from registry_api.chain import ChainVerifier, EsploraChainVerifier, IssuanceCommitment
from registry_api.db import SessionLocal
from registry_api.models import Action, Asset
from registry_api.serialized_fragments import refresh_asset_serialized_fragments
from registry_api.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class BackfillSummary:
    checked: int = 0
    verified: int = 0
    updated: int = 0
    failed: int = 0


def backfill_issuance_references(
    db: Session,
    verifier: ChainVerifier,
    *,
    dry_run: bool = False,
    batch_size: int = 100,
    limit: int | None = None,
) -> BackfillSummary:
    if batch_size < 1 or (limit is not None and limit < 1):
        raise ValueError("batch_size and limit must be positive")
    summary = BackfillSummary()
    after: UUID | None = None
    while limit is None or summary.checked < limit:
        query = select(Asset.asset_uuid, Asset.asset_id).where(
            or_(Asset.issuance_txid.is_(None), Asset.issuance_prevout_txid.is_(None))
        )
        if after is not None:
            query = query.where(Asset.asset_uuid > after)
        remaining = (
            batch_size if limit is None else min(batch_size, limit - summary.checked)
        )
        rows = db.execute(query.order_by(Asset.asset_uuid).limit(remaining)).all()
        if not rows:
            break
        for asset_uuid, asset_id in rows:
            summary.checked += 1
            try:
                payload = db.scalar(
                    select(Action.action)
                    .where(
                        Action.asset_uuid == asset_uuid,
                        Action.operation.in_(("legacy_register", "register")),
                    )
                    .order_by(Action.audit_sequence)
                    .limit(1)
                )
                hash_hex = (
                    payload.get("contract_hash") if isinstance(payload, dict) else None
                )
                if not isinstance(hash_hex, str):
                    raise TypeError("registration has no preserved contract hash")
                issuance = verifier.verify_issuance_commitment(
                    IssuanceCommitment(asset_id=asset_id, contract_hash=hash_hex)
                )
                if (
                    issuance is None
                    or issuance.issuance_txin is None
                    or issuance.issuance_prevout is None
                ):
                    raise ValueError(
                        "chain verifier returned incomplete issuance references"
                    )
                # Re-read under a row lock after network IO so concurrent runs
                # cannot overwrite a reference filled while verification ran.
                asset = db.scalar(
                    select(Asset)
                    .where(Asset.asset_uuid == asset_uuid)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if asset is None:
                    raise ValueError("asset disappeared during verification")
                columns = issuance.asset_columns()
                for column, value in columns.items():
                    current = getattr(asset, column)
                    if current is not None and current != value:
                        raise ValueError(
                            "verified issuance conflicts with a stored reference"
                        )
                summary.verified += 1
                if dry_run:
                    db.rollback()
                    continue
                db.execute(
                    update(Asset)
                    .where(Asset.asset_uuid == asset_uuid)
                    .values(**columns, updated_at=Asset.updated_at)
                )
                db.expire(asset)
                refresh_asset_serialized_fragments(db, asset)
                db.commit()
                summary.updated += 1
            except Exception:
                db.rollback()
                summary.failed += 1
                logger.exception("Could not backfill issuance for %s", asset_id)
        after = rows[-1].asset_uuid
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and verify references without writing to the database.",
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--limit", type=int, help="Maximum number of missing-reference assets to check."
    )
    args = parser.parse_args()
    if args.batch_size < 1 or (args.limit is not None and args.limit < 1):
        parser.error("--batch-size and --limit must be positive")
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    # Settings resolves network defaults before returning a validated instance.
    assert settings.esplora_url is not None
    verifier = EsploraChainVerifier(
        settings.esplora_url, timeout=settings.http_timeout_seconds
    )
    with SessionLocal() as db:
        summary = backfill_issuance_references(
            db,
            verifier,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            limit=args.limit,
        )
    print(
        f"checked={summary.checked} verified={summary.verified} "
        f"updated={summary.updated} failed={summary.failed}"
    )
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
