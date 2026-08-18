from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from registry_api.action_writer import new_action
from registry_api.constants import Actor, Operation
from registry_api.errors import ErrorCode, RegistryError
from registry_api.legacy_response import legacy_response_from_asset
from registry_api.models import Action, Asset
from registry_api.serialized_fragments import (
    delete_asset_serialized_fragments,
    legacy_all_json_bytes,
)
from registry_api.signatures import verify_legacy_deletion_signature
from registry_api.validation import normalize_asset_id


def get_legacy_asset(db: Session, asset_id: str) -> dict[str, Any]:
    asset = db.scalar(_active_asset_query(asset_id))
    if asset is None:
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "asset not found",
            {"asset_id": asset_id},
            status_code=404,
        )
    return legacy_response_from_asset(asset, _registration_payload(db, asset))


def list_legacy_assets(db: Session) -> dict[str, Any]:
    assets = _active_assets(db)
    registration_payloads = _registration_payloads(db, assets)
    return {
        asset.asset_id: legacy_response_from_asset(
            asset, registration_payloads.get(asset.asset_uuid)
        )
        for asset in assets
    }


def list_legacy_assets_json_bytes(db: Session) -> bytes:
    return legacy_all_json_bytes(db)


def _active_assets(db: Session) -> list[Asset]:
    assets = (
        db.scalars(
            select(Asset).where(Asset.status == "active").order_by(Asset.asset_id.asc())
        )
        .unique()
        .all()
    )
    return list(assets)


def deregister_legacy_asset(db: Session, asset_id: str, signature: str) -> str:
    normalized_asset_id = normalize_asset_id(asset_id)
    asset = db.scalar(_active_asset_query(normalized_asset_id))
    if asset is None:
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "asset not found",
            {"asset_id": normalized_asset_id},
            status_code=404,
        )

    verify_legacy_deletion_signature(
        asset.current_issuer_pubkey, signature, normalized_asset_id
    )

    action = new_action(
        asset,
        actor=Actor.ISSUER,
        operation=Operation.LEGACY_DEREGISTER,
        payload={
            "asset_id": asset.asset_id,
            "message": f"remove {asset.asset_id} from registry",
        },
        signature=signature,
        verified_pubkey=asset.current_issuer_pubkey,
    )
    asset.status = "deregistered"
    delete_asset_serialized_fragments(db, asset)

    db.add(action)
    db.flush()
    from registry_api.icons import obsolete_asset_icon_proposals

    obsolete_asset_icon_proposals(db, asset, action)
    db.commit()
    return "Asset deleted"


def _active_asset_query(asset_id: str) -> Select[tuple[Asset]]:
    return select(Asset).where(Asset.asset_id == asset_id, Asset.status == "active")


def _registration_payload(db: Session, asset: Asset) -> dict[str, Any] | None:
    action = db.scalar(
        select(Action)
        .where(
            Action.asset_uuid == asset.asset_uuid,
            Action.operation == Operation.LEGACY_REGISTER,
        )
        .order_by(Action.audit_sequence.asc())
        .limit(1)
    )
    payload = action.action if action is not None else None
    if isinstance(payload, dict) and isinstance(payload.get("request"), dict):
        return payload["request"]
    return None


def _registration_payloads(
    db: Session, assets: list[Asset]
) -> dict[Any, dict[str, Any]]:
    if not assets:
        return {}
    asset_uuids = [asset.asset_uuid for asset in assets]
    rows = db.execute(
        select(Action.asset_uuid, Action.action)
        .where(
            Action.asset_uuid.in_(asset_uuids),
            Action.operation == Operation.LEGACY_REGISTER,
        )
        .order_by(Action.asset_uuid.asc(), Action.audit_sequence.asc())
    ).all()
    payloads: dict[Any, dict[str, Any]] = {}
    for asset_uuid, action in rows:
        if asset_uuid in payloads:
            continue
        if isinstance(action, dict) and isinstance(action.get("request"), dict):
            payloads[asset_uuid] = action["request"]
    return payloads
