from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from registry_api.action_writer import new_action
from registry_api.admin_actions import VerifiedAdminAction, require_admin_permission, verify_admin_asset_action
from registry_api.constants import Actor
from registry_api.errors import ErrorCode, RegistryError
from registry_api.models import Action, Asset, AssetAdminAnnotation
from registry_api.schemas import (
    AssetResponse,
    ForceDelistAssetAction,
    ForceRelistAssetAction,
    UpdateAdminAnnotationsAction,
)
from registry_api.serialized_fragments import refresh_asset_serialized_fragments
from registry_api.validation import normalize_asset_id
from registry_api.v2_assets import asset_response_from_row


def update_admin_annotations(
    db: Session,
    *,
    asset_id: str,
    payload: bytes,
    signature: str,
    now: datetime | None = None,
    freshness_window: timedelta | None = None,
) -> AssetResponse:
    if freshness_window is None:
        verified = verify_admin_asset_action(db, payload=payload, signature=signature, now=now)
    else:
        verified = verify_admin_asset_action(
            db,
            payload=payload,
            signature=signature,
            now=now,
            freshness_window=freshness_window,
        )
    action = verified.action
    if not isinstance(action, UpdateAdminAnnotationsAction):
        raise RegistryError(ErrorCode.VALIDATION_ERROR, "admin action operation must be update_admin_annotations")
    if action.asset_id != asset_id.lower():
        _raise_asset_id_mismatch(asset_id, action.asset_id)
    return _apply_admin_annotations(db, asset_id=asset_id, verified=verified)


def submit_admin_asset_action(
    db: Session,
    *,
    asset_id: str,
    payload: bytes,
    signature: str,
    now: datetime | None = None,
    freshness_window: timedelta | None = None,
) -> AssetResponse:
    if freshness_window is None:
        verified = verify_admin_asset_action(db, payload=payload, signature=signature, now=now)
    else:
        verified = verify_admin_asset_action(
            db,
            payload=payload,
            signature=signature,
            now=now,
            freshness_window=freshness_window,
        )
    action = verified.action
    if action.asset_id != asset_id.lower():
        _raise_asset_id_mismatch(asset_id, action.asset_id)
    if isinstance(action, UpdateAdminAnnotationsAction):
        return _apply_admin_annotations(db, asset_id=asset_id, verified=verified)
    if isinstance(action, (ForceDelistAssetAction, ForceRelistAssetAction)):
        return _apply_forced_delist_state(db, asset_id=asset_id, verified=verified)
    raise RegistryError(
        ErrorCode.UNSUPPORTED_OPERATION,
        "admin asset action operation is not supported",
        {"operation": action.operation},
    )


def _apply_admin_annotations(db: Session, *, asset_id: str, verified: VerifiedAdminAction) -> AssetResponse:
    require_admin_permission(verified.actor, "annotate_assets")
    signed_action = verified.action
    if not isinstance(signed_action, UpdateAdminAnnotationsAction):
        raise RegistryError(ErrorCode.VALIDATION_ERROR, "admin action operation must be update_admin_annotations")

    try:
        normalized_asset_id = normalize_asset_id(asset_id)
    except ValueError as exc:
        raise RegistryError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc

    asset = db.scalar(select(Asset).where(Asset.asset_id == normalized_asset_id, Asset.status == "active"))
    if asset is None:
        raise RegistryError(ErrorCode.ASSET_NOT_FOUND, "asset not found", {"asset_id": asset_id}, status_code=404)

    changes = signed_action.changes.model_dump(exclude_unset=True)
    annotations = _get_or_create_annotations(db, asset)
    existing = _existing_admin_asset_action(db, verified, signed_action.nonce)
    if existing is not None:
        db.refresh(asset)
        return asset_response_from_row(db, asset)
    _reject_no_op_annotations(annotations, changes)

    action = _new_admin_asset_action(asset, verified)

    try:
        db.add(action)
        db.flush()
        for field, value in changes.items():
            setattr(annotations, field, value)
        _mark_admin_asset_changed(asset, annotations, action, verified)
        db.flush()
        refresh_asset_serialized_fragments(db, asset)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(asset)
    return asset_response_from_row(db, asset)


def _apply_forced_delist_state(db: Session, *, asset_id: str, verified: VerifiedAdminAction) -> AssetResponse:
    require_admin_permission(verified.actor, "delist_assets")
    signed_action = verified.action
    if not isinstance(signed_action, (ForceDelistAssetAction, ForceRelistAssetAction)):
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "admin action operation must be force_delist_asset or force_relist_asset",
        )

    try:
        normalized_asset_id = normalize_asset_id(asset_id)
    except ValueError as exc:
        raise RegistryError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc

    asset = db.scalar(select(Asset).where(Asset.asset_id == normalized_asset_id, Asset.status == "active"))
    if asset is None:
        raise RegistryError(ErrorCode.ASSET_NOT_FOUND, "asset not found", {"asset_id": asset_id}, status_code=404)

    annotations = _get_or_create_annotations(db, asset)
    existing = _existing_admin_asset_action(db, verified, signed_action.nonce)
    if existing is not None:
        db.refresh(asset)
        return asset_response_from_row(db, asset)
    requested_delisted = isinstance(signed_action, ForceDelistAssetAction)
    if annotations.delisted == requested_delisted:
        raise RegistryError(ErrorCode.NO_OP_ACTION, "action would not change registry state")

    action = _new_admin_asset_action(asset, verified)

    try:
        db.add(action)
        db.flush()
        annotations.delisted = requested_delisted
        if signed_action.reason is not None:
            annotations.admin_notes = signed_action.reason
        _mark_admin_asset_changed(asset, annotations, action, verified)
        db.flush()
        refresh_asset_serialized_fragments(db, asset)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(asset)
    return asset_response_from_row(db, asset)


def _get_or_create_annotations(db: Session, asset: Asset) -> AssetAdminAnnotation:
    annotations = db.scalar(select(AssetAdminAnnotation).where(AssetAdminAnnotation.asset_uuid == asset.asset_uuid))
    if annotations is None:
        annotations = AssetAdminAnnotation(asset_uuid=asset.asset_uuid)
        db.add(annotations)
        db.flush()
    return annotations


def _existing_admin_asset_action(db: Session, verified: VerifiedAdminAction, nonce: str) -> Action | None:
    existing = db.scalar(
        select(Action).where(
            Action.actor == Actor.ADMIN,
            Action.verified_pubkey == verified.actor_pubkey,
            Action.nonce == nonce,
        )
    )
    if existing is not None:
        if existing.action == verified.parsed:
            return existing
        raise RegistryError(
            ErrorCode.NONCE_CONFLICT,
            "nonce has already been used for a different admin action",
            status_code=409,
        )
    return None


def _reject_no_op_annotations(annotations: AssetAdminAnnotation, changes: dict) -> None:
    if not changes:
        raise RegistryError(ErrorCode.NO_OP_ACTION, "action would not change registry state")
    if all(getattr(annotations, field) == value for field, value in changes.items()):
        raise RegistryError(ErrorCode.NO_OP_ACTION, "action would not change registry state")


def _new_admin_asset_action(asset: Asset, verified: VerifiedAdminAction) -> Action:
    return new_action(
        asset,
        actor=Actor.ADMIN,
        operation=verified.action.operation,
        payload=verified.parsed,
        signature=verified.signature,
        nonce=verified.action.nonce,
        issuer_timestamp=verified.action.timestamp,
        verified_pubkey=verified.actor_pubkey,
        admin_id=str(verified.actor.admin_uuid),
    )


def _mark_admin_asset_changed(
    asset: Asset,
    annotations: AssetAdminAnnotation,
    action: Action,
    verified: VerifiedAdminAction,
) -> None:
    annotations.last_admin_action_uuid = action.action_uuid
    annotations.updated_by_admin_id = str(verified.actor.admin_uuid)
    annotations.updated_at = datetime.now(UTC)
    asset.updated_at = datetime.now(UTC)


def _raise_asset_id_mismatch(url_asset_id: str, action_asset_id: str) -> None:
    raise RegistryError(
        ErrorCode.ASSET_ID_MISMATCH,
        "URL asset_id does not match signed action asset_id",
        {"url_asset_id": url_asset_id, "action_asset_id": action_asset_id},
    )
