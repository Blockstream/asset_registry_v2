from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from registry_api.action_writer import new_action
from registry_api.constants import Actor, IconProposalStatus, Operation
from registry_api.db import SessionLocal
from registry_api.errors import ErrorCode, RegistryError
from registry_api.icons import decode_legacy_icon
from registry_api.models import Asset, AssetIconProposal
from registry_api.serialized_fragments import refresh_asset_serialized_fragments
from registry_api.validation import normalize_asset_id


@dataclass(frozen=True)
class PreparedLegacyIcon:
    asset: Asset
    image_data: bytes
    icon_hash: str
    deviations: tuple[str, ...]


def import_legacy_icons(
    db: Session,
    icon_map: dict[str, str],
    *,
    dry_run: bool = False,
    skip_missing: bool = False,
) -> dict[str, object]:
    prepared: list[PreparedLegacyIcon] = []
    missing: list[str] = []
    skipped_existing = 0
    deviations = {"dimensions": 0, "size": 0, "alpha": 0}

    normalized_entries: list[tuple[str, str]] = []
    for raw_asset_id, encoded in icon_map.items():
        try:
            asset_id = normalize_asset_id(raw_asset_id)
        except ValueError as exc:
            raise RegistryError(
                ErrorCode.VALIDATION_ERROR,
                f"invalid icons.json asset ID: {raw_asset_id}",
            ) from exc
        if not isinstance(encoded, str):
            raise RegistryError(
                ErrorCode.INVALID_ICON,
                f"icon value for {asset_id} must be a Base64 string",
            )
        normalized_entries.append((asset_id, encoded))

    assets = db.scalars(
        select(Asset).where(
            Asset.asset_id.in_([asset_id for asset_id, _encoded in normalized_entries]),
            Asset.status == "active",
        )
    ).all()
    assets_by_id = {asset.asset_id: asset for asset in assets}
    current_rows = db.scalars(
        select(AssetIconProposal)
        .join(
            Asset,
            Asset.active_icon_proposal_uuid == AssetIconProposal.icon_proposal_uuid,
        )
        .where(
            Asset.asset_uuid.in_([asset.asset_uuid for asset in assets]),
            AssetIconProposal.status == IconProposalStatus.APPROVED,
            AssetIconProposal.image_data.is_not(None),
        )
    ).all()
    current_by_asset_uuid = {row.asset_uuid: row for row in current_rows}

    for asset_id, encoded in normalized_entries:
        image_data, icon_hash, row_deviations = decode_legacy_icon(encoded)
        asset = assets_by_id.get(asset_id)
        if asset is None:
            missing.append(asset_id)
            continue
        current = current_by_asset_uuid.get(asset.asset_uuid)
        if current is not None:
            if current.icon_hash == icon_hash:
                skipped_existing += 1
                continue
            raise RegistryError(
                ErrorCode.ASSET_CONFLICT,
                "asset already has a different approved icon",
                {
                    "asset_id": asset_id,
                    "current_icon_hash": current.icon_hash,
                    "import_icon_hash": icon_hash,
                },
                status_code=409,
            )
        for deviation in row_deviations:
            deviations[deviation] += 1
        prepared.append(
            PreparedLegacyIcon(
                asset=asset,
                image_data=image_data,
                icon_hash=icon_hash,
                deviations=tuple(row_deviations),
            )
        )

    if missing and not skip_missing:
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "icons.json contains assets that are not active in this registry",
            {"missing_count": len(missing), "missing_asset_ids": missing[:100]},
            status_code=404,
        )

    summary: dict[str, object] = {
        "input_count": len(icon_map),
        "imported_count": 0 if dry_run else len(prepared),
        "would_import_count": len(prepared),
        "skipped_existing_count": skipped_existing,
        "skipped_missing_count": len(missing) if skip_missing else 0,
        "grandfathered_deviations": deviations,
        "dry_run": dry_run,
    }
    if dry_run:
        return summary

    now = datetime.now(UTC)
    try:
        for item in prepared:
            payload = {
                "asset_id": item.asset.asset_id,
                "icon_hash": item.icon_hash,
                "operation": Operation.IMPORT_LEGACY_ICON,
                "source": "legacy_icons_json",
            }
            action = new_action(
                item.asset,
                actor=Actor.SYSTEM,
                operation=Operation.IMPORT_LEGACY_ICON,
                payload=payload,
            )
            proposal = AssetIconProposal(
                asset_uuid=item.asset.asset_uuid,
                icon_hash=item.icon_hash,
                image_data=item.image_data,
                status=IconProposalStatus.APPROVED,
                submission_method="legacy_import",
                proposed_by_action=action,
                decided_by_action=action,
                proposed_at=now,
                decided_at=now,
            )
            item.asset.updated_at = now
            db.add_all([action, proposal])
            db.flush()
            item.asset.icon = proposal
            db.flush()
            refresh_asset_serialized_fragments(db, item.asset)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return summary


def load_icon_map(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(
            ErrorCode.INVALID_JSON, f"could not read legacy icon map: {path}"
        ) from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RegistryError(
            ErrorCode.INVALID_JSON,
            "legacy icon map must be a JSON object keyed by asset ID",
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import approved icons from a legacy icons.json map."
    )
    parser.add_argument(
        "--input", type=Path, required=True, help="Path to the legacy icons.json file."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report without writing to the database.",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip asset IDs that are not active in this registry instead of failing the import.",
    )
    args = parser.parse_args()
    try:
        icon_map = load_icon_map(args.input)
        with SessionLocal() as db:
            summary = import_legacy_icons(
                db,
                icon_map,
                dry_run=args.dry_run,
                skip_missing=args.skip_missing,
            )
        print(json.dumps(summary, sort_keys=True))
        return 0
    except RegistryError as exc:
        print(
            json.dumps(
                {"error": exc.error, "message": exc.message, "details": exc.details},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
