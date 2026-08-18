from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from registry_api.action_writer import new_action
from registry_api.canonical_json import require_canonical_json
from registry_api.constants import Actor
from registry_api.errors import ErrorCode, RegistryError, clean_pydantic_errors
from registry_api.models import (
    Action,
    Asset,
    AssetCategoryTag,
    AssetCustomAttribute,
    AssetMutableMetadata,
    AssetTradingVenue,
    IssuerPubkeyHistory,
)
from registry_api.schemas import (
    DeleteCustomFieldAction,
    DeregisterAction,
    IssuerAction,
    IssuerActionResponse,
    LatestActionHashResponse,
    MutableMetadata,
    ReplaceCategoryTagsAction,
    ReplaceCustomAction,
    ReplaceTradingVenuesAction,
    RotateIssuerPubkeyAction,
    SetCustomFieldAction,
)
from registry_api.serialized_fragments import (
    delete_asset_serialized_fragments,
    refresh_asset_serialized_fragments,
)
from registry_api.signatures import (
    validate_signature_encoding,
    verify_canonical_payload_signature,
)
from registry_api.validation import normalize_asset_id
from registry_api.v2_assets import asset_response_from_row

ISSUER_ACTION_ADAPTER = TypeAdapter(IssuerAction)
FRESHNESS_WINDOW = timedelta(minutes=5)


def submit_issuer_action(
    db: Session,
    *,
    asset_id: str,
    payload: bytes,
    signature: str,
    now: datetime | None = None,
    freshness_window: timedelta = FRESHNESS_WINDOW,
) -> IssuerActionResponse:
    validate_signature_encoding(signature)
    parsed = require_canonical_json(payload)
    try:
        action = ISSUER_ACTION_ADAPTER.validate_python(parsed)
    except ValidationError as exc:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "issuer action failed validation",
            {"errors": clean_pydantic_errors(exc)},
        ) from exc

    if action.asset_id != asset_id.lower():
        raise RegistryError(
            ErrorCode.ASSET_ID_MISMATCH,
            "URL asset_id does not match signed action asset_id",
            {"url_asset_id": asset_id, "action_asset_id": action.asset_id},
        )

    asset = db.scalar(
        select(Asset)
        .where(Asset.asset_id == action.asset_id)
        .where(Asset.status == "active")
    )
    if asset is None:
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "asset not found",
            {"asset_id": action.asset_id},
            status_code=404,
        )
    if (
        asset.contract_version == 0
        and asset.initial_issuer_pubkey_source != "migrated_legacy_record"
    ):
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "asset not found; legacy assets must be migrated before v2 issuer actions",
            {"asset_id": action.asset_id},
            status_code=404,
        )
    if asset.status != "active" and not isinstance(action, DeregisterAction):
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "asset not found",
            {"asset_id": action.asset_id},
            status_code=404,
        )

    normalized_action = action.model_dump(mode="json")
    existing = _existing_nonce_action(db, asset, action.nonce)
    if existing is not None:
        if existing.action == normalized_action:
            return IssuerActionResponse(
                status="idempotent_retry",
                audit_entry=_audit_entry(existing),
                asset=None,
            )
        raise RegistryError(
            ErrorCode.NONCE_CONFLICT,
            "nonce has already been used for a different issuer action",
            status_code=409,
        )

    latest_action = _latest_chain_action(db, asset)
    _ensure_prev_action_hash(asset, action.prev_action_hash, latest_action)
    _check_freshness(
        db, asset, action.timestamp, now=now, freshness_window=freshness_window
    )
    verify_canonical_payload_signature(asset.current_issuer_pubkey, signature, payload)
    _reject_no_op_action(db, asset, action)

    row = new_action(
        asset,
        actor=Actor.ISSUER,
        operation=action.operation,
        payload=normalized_action,
        signature=signature,
        nonce=action.nonce,
        issuer_timestamp=action.timestamp,
        verified_pubkey=asset.current_issuer_pubkey,
        participates_in_hash_chain=True,
    )

    try:
        db.add(row)
        db.flush()
        _apply_action(db, asset, action, row)
        db.flush()
        if isinstance(action, DeregisterAction):
            delete_asset_serialized_fragments(db, asset)
        else:
            refresh_asset_serialized_fragments(db, asset)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RegistryError(
            ErrorCode.NONCE_CONFLICT,
            "nonce has already been used for this asset",
            status_code=409,
        ) from exc
    except Exception:
        db.rollback()
        raise

    db.refresh(row)
    db.refresh(asset)
    return IssuerActionResponse(
        status="applied",
        audit_entry=_audit_entry(row),
        asset=asset_response_from_row(db, asset),
    )


def get_latest_action_hash(db: Session, asset_id: str) -> LatestActionHashResponse:
    try:
        normalized_asset_id = normalize_asset_id(asset_id)
    except ValueError as exc:
        raise RegistryError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    asset = db.scalar(
        select(Asset)
        .where(Asset.asset_id == normalized_asset_id)
        .where(Asset.status == "active")
    )
    if asset is None:
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "asset not found",
            {"asset_id": normalized_asset_id},
            status_code=404,
        )
    if (
        asset.contract_version == 0
        and asset.initial_issuer_pubkey_source != "migrated_legacy_record"
    ):
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "asset not found; legacy assets must be migrated before v2 issuer actions",
            {"asset_id": asset.asset_id},
            status_code=404,
        )
    latest = _latest_chain_action(db, asset)
    if latest is None or latest.action_hash is None:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "asset has no latest action hash",
            {"asset_id": asset.asset_id},
            status_code=500,
        )
    return LatestActionHashResponse(
        asset_id=asset.asset_id,
        action_hash=latest.action_hash,
        audit_id=latest.audit_sequence,
        operation=latest.operation,
        server_received_at=latest.server_received_at,
    )


def _apply_action(db: Session, asset: Asset, action: IssuerAction, row: Action) -> None:
    asset.updated_at = datetime.now(UTC)
    if isinstance(action, ReplaceCategoryTagsAction):
        _apply_replace_category_tags(db, asset, action, row)
        return
    if isinstance(action, ReplaceTradingVenuesAction):
        _apply_replace_trading_venues(db, asset, action, row)
        return
    if isinstance(action, ReplaceCustomAction):
        _apply_replace_custom(db, asset, action, row)
        return
    if isinstance(action, SetCustomFieldAction):
        _apply_set_custom_field(db, asset, action, row)
        return
    if isinstance(action, DeleteCustomFieldAction):
        _apply_delete_custom_field(db, asset, action, row)
        return
    if isinstance(action, DeregisterAction):
        from registry_api.icons import obsolete_asset_icon_proposals

        asset.status = "deregistered"
        obsolete_asset_icon_proposals(db, asset, row)
        return
    if isinstance(action, RotateIssuerPubkeyAction):
        _apply_rotation(db, asset, action, row)
        return
    raise RegistryError(
        ErrorCode.UNSUPPORTED_OPERATION,
        "issuer action operation is not supported yet",
        {"operation": action.operation},
    )


def _reject_no_op_action(db: Session, asset: Asset, action: IssuerAction) -> None:
    if isinstance(action, ReplaceCategoryTagsAction):
        _ensure_mutable_schema(asset, action.mutable_schema_version)
        current = [
            row.tag
            for row in db.scalars(
                select(AssetCategoryTag)
                .where(AssetCategoryTag.asset_uuid == asset.asset_uuid)
                .order_by(AssetCategoryTag.position.asc())
            )
        ]
        if current == action.category_tags:
            raise RegistryError(
                ErrorCode.NO_OP_ACTION, "action would not change registry state"
            )
        return
    if isinstance(action, ReplaceTradingVenuesAction):
        _ensure_mutable_schema(asset, action.mutable_schema_version)
        current = [
            {"venue": row.name, "url": row.url}
            for row in db.scalars(
                select(AssetTradingVenue)
                .where(AssetTradingVenue.asset_uuid == asset.asset_uuid)
                .order_by(AssetTradingVenue.position.asc())
            )
        ]
        submitted = [venue.model_dump() for venue in action.trading_venues]
        if current == submitted:
            raise RegistryError(
                ErrorCode.NO_OP_ACTION, "action would not change registry state"
            )
        return
    if isinstance(action, ReplaceCustomAction):
        _ensure_mutable_schema(asset, action.mutable_schema_version)
        if _current_custom(db, asset) == action.custom:
            raise RegistryError(
                ErrorCode.NO_OP_ACTION, "action would not change registry state"
            )
        return
    if isinstance(action, SetCustomFieldAction):
        _ensure_mutable_schema(asset, action.mutable_schema_version)
        current = db.scalar(
            select(AssetCustomAttribute).where(
                AssetCustomAttribute.asset_uuid == asset.asset_uuid,
                AssetCustomAttribute.name == action.custom_key,
            )
        )
        if current is not None and current.value == action.value:
            raise RegistryError(
                ErrorCode.NO_OP_ACTION, "action would not change registry state"
            )
        return
    if isinstance(action, DeleteCustomFieldAction):
        _ensure_mutable_schema(asset, action.mutable_schema_version)
        if _custom_attribute(db, asset, action.custom_key) is None:
            raise RegistryError(
                ErrorCode.NO_OP_ACTION, "action would not change registry state"
            )
        return
    if isinstance(action, DeregisterAction):
        if asset.status == "deregistered":
            raise RegistryError(
                ErrorCode.NO_OP_ACTION, "action would not change registry state"
            )
        return
    if isinstance(action, RotateIssuerPubkeyAction):
        if asset.current_issuer_pubkey == action.new_issuer_pubkey:
            raise RegistryError(
                ErrorCode.NO_OP_ACTION, "action would not change registry state"
            )


def _apply_replace_category_tags(
    db: Session, asset: Asset, action: ReplaceCategoryTagsAction, row: Action
) -> None:
    _ensure_mutable_schema(asset, action.mutable_schema_version)
    mutable = MutableMetadata(
        category_tags=action.category_tags, trading_venues=[], custom={}
    )
    db.execute(
        delete(AssetCategoryTag).where(AssetCategoryTag.asset_uuid == asset.asset_uuid)
    )
    for position, tag in enumerate(mutable.category_tags):
        db.add(
            AssetCategoryTag(
                asset_uuid=asset.asset_uuid,
                tag=tag,
                position=position,
                updated_by_action_uuid=row.action_uuid,
            )
        )
    _touch_mutable(db, asset, row)


def _apply_replace_trading_venues(
    db: Session, asset: Asset, action: ReplaceTradingVenuesAction, row: Action
) -> None:
    _ensure_mutable_schema(asset, action.mutable_schema_version)
    mutable = MutableMetadata(
        category_tags=[], trading_venues=action.trading_venues, custom={}
    )
    db.execute(
        delete(AssetTradingVenue).where(
            AssetTradingVenue.asset_uuid == asset.asset_uuid
        )
    )
    for position, venue in enumerate(mutable.trading_venues):
        db.add(
            AssetTradingVenue(
                asset_uuid=asset.asset_uuid,
                name=venue.venue,
                url=venue.url,
                position=position,
                updated_by_action_uuid=row.action_uuid,
            )
        )
    _touch_mutable(db, asset, row)


def _apply_replace_custom(
    db: Session, asset: Asset, action: ReplaceCustomAction, row: Action
) -> None:
    _ensure_mutable_schema(asset, action.mutable_schema_version)
    db.execute(
        delete(AssetCustomAttribute).where(
            AssetCustomAttribute.asset_uuid == asset.asset_uuid
        )
    )
    for key, value in action.custom.items():
        db.add(
            AssetCustomAttribute(
                asset_uuid=asset.asset_uuid,
                name=key,
                value=value,
                updated_by_action_uuid=row.action_uuid,
            )
        )
    _touch_mutable(db, asset, row)


def _apply_set_custom_field(
    db: Session, asset: Asset, action: SetCustomFieldAction, row: Action
) -> None:
    _ensure_mutable_schema(asset, action.mutable_schema_version)
    db.execute(
        delete(AssetCustomAttribute).where(
            AssetCustomAttribute.asset_uuid == asset.asset_uuid,
            AssetCustomAttribute.name == action.custom_key,
        )
    )
    db.add(
        AssetCustomAttribute(
            asset_uuid=asset.asset_uuid,
            name=action.custom_key,
            value=action.value,
            updated_by_action_uuid=row.action_uuid,
        )
    )
    _touch_mutable(db, asset, row)


def _apply_delete_custom_field(
    db: Session, asset: Asset, action: DeleteCustomFieldAction, row: Action
) -> None:
    _ensure_mutable_schema(asset, action.mutable_schema_version)
    db.execute(
        delete(AssetCustomAttribute).where(
            AssetCustomAttribute.asset_uuid == asset.asset_uuid,
            AssetCustomAttribute.name == action.custom_key,
        )
    )
    _touch_mutable(db, asset, row)


def _apply_rotation(
    db: Session, asset: Asset, action: RotateIssuerPubkeyAction, row: Action
) -> None:
    current = db.scalar(
        select(IssuerPubkeyHistory).where(
            IssuerPubkeyHistory.asset_uuid == asset.asset_uuid,
            IssuerPubkeyHistory.valid_until_action_uuid.is_(None),
        )
    )
    if current is not None:
        current.valid_until_action_uuid = row.action_uuid
    asset.current_issuer_pubkey = action.new_issuer_pubkey
    db.add(
        IssuerPubkeyHistory(
            asset_uuid=asset.asset_uuid,
            pubkey=action.new_issuer_pubkey,
            valid_from_action_uuid=row.action_uuid,
        )
    )


def _check_freshness(
    db: Session,
    asset: Asset,
    timestamp: datetime,
    *,
    now: datetime | None,
    freshness_window: timedelta,
) -> None:
    current_time = now or datetime.now(UTC)
    timestamp_utc = timestamp.astimezone(UTC)
    if abs(current_time - timestamp_utc) > freshness_window:
        raise RegistryError(
            ErrorCode.STALE_TIMESTAMP,
            "issuer action timestamp is outside the accepted freshness window",
        )

    previous = db.scalar(
        select(Action.issuer_timestamp)
        .where(Action.asset_uuid == asset.asset_uuid, Action.actor == Actor.ISSUER)
        .order_by(Action.issuer_timestamp.desc())
        .limit(1)
    )
    if previous is not None and timestamp_utc < previous.astimezone(UTC):
        raise RegistryError(
            ErrorCode.STALE_TIMESTAMP,
            "issuer action timestamp is older than the latest accepted issuer action",
        )


def _ensure_mutable_schema(asset: Asset, schema_version: int) -> None:
    if schema_version != asset.mutable_schema_version:
        raise RegistryError(
            ErrorCode.MUTABLE_SCHEMA_VERSION_MISMATCH,
            "mutable schema version does not match current asset state",
            {"current": asset.mutable_schema_version, "submitted": schema_version},
        )


def _touch_mutable(db: Session, asset: Asset, row: Action) -> None:
    metadata = db.scalar(
        select(AssetMutableMetadata).where(
            AssetMutableMetadata.asset_uuid == asset.asset_uuid
        )
    )
    if metadata is not None:
        metadata.updated_at = datetime.now(UTC)
        metadata.updated_by_action_uuid = row.action_uuid


def _current_custom(db: Session, asset: Asset) -> dict[str, Any]:
    return {
        row.name: row.value
        for row in db.scalars(
            select(AssetCustomAttribute)
            .where(AssetCustomAttribute.asset_uuid == asset.asset_uuid)
            .order_by(AssetCustomAttribute.name.asc())
        )
    }


def _custom_attribute(
    db: Session, asset: Asset, key: str
) -> AssetCustomAttribute | None:
    return db.scalar(
        select(AssetCustomAttribute).where(
            AssetCustomAttribute.asset_uuid == asset.asset_uuid,
            AssetCustomAttribute.name == key,
        )
    )


def _existing_nonce_action(db: Session, asset: Asset, nonce: str) -> Action | None:
    return db.scalar(
        select(Action).where(
            Action.asset_uuid == asset.asset_uuid,
            Action.actor == Actor.ISSUER,
            Action.nonce == nonce,
        )
    )


def _latest_chain_action(db: Session, asset: Asset) -> Action | None:
    return db.scalar(
        select(Action)
        .where(Action.asset_uuid == asset.asset_uuid, Action.action_hash.is_not(None))
        .order_by(Action.audit_sequence.desc())
        .limit(1)
    )


def _ensure_prev_action_hash(
    asset: Asset, submitted: str, latest: Action | None
) -> None:
    expected = latest.action_hash if latest is not None else None
    if expected is None or submitted != expected:
        raise RegistryError(
            ErrorCode.PREV_ACTION_HASH_MISMATCH,
            "prev_action_hash does not match latest accepted action",
            {
                "asset_id": asset.asset_id,
                "expected_prev_action_hash": expected,
                "submitted_prev_action_hash": submitted,
            },
            status_code=409,
        )


def _audit_entry(action: Action):
    from registry_api.schemas import AuditEntry

    return AuditEntry(
        audit_id=action.audit_sequence,
        server_received_at=action.server_received_at,
        actor=action.actor,
        verified_pubkey=action.verified_pubkey,
        admin_id=action.admin_id,
        action=action.action,
        action_hash=action.action_hash,
        signature=action.signature,
    )
