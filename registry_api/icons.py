from __future__ import annotations

import base64
import binascii
import hashlib
import io
import math
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from importlib.resources import files
from typing import Literal

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError
from pydantic_core import to_json
from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from registry_api.action_writer import new_action
from registry_api.admin import (
    _existing_admin_asset_action,
    _get_or_create_annotations,
    _mark_admin_asset_changed,
)
from registry_api.admin_actions import (
    FRESHNESS_WINDOW,
    VerifiedAdminAction,
    get_active_admin,
    require_admin_permission,
    verify_admin_asset_action,
)
from registry_api.audit import audit_entry_from_row
from registry_api.canonical_json import canonical_json_bytes, require_canonical_json
from registry_api.constants import (
    Actor,
    IconProposalStatus,
    LIQUID_MAINNET_POLICY_ASSET_ID,
    Operation,
)
from registry_api.errors import ErrorCode, RegistryError, clean_pydantic_errors
from registry_api.issuer_actions import (
    _check_freshness,
    _ensure_prev_action_hash,
    _latest_chain_action,
)
from registry_api.models import Action, Asset, AssetIconProposal, IssuerPubkeyHistory
from registry_api.schemas import (
    ApproveIconAction,
    IconProposalResponse,
    IconProposalSummary,
    IssuerIconProposal,
    IssuerIconProposalListResponse,
    IssuerIconProposalSearchRequest,
    PendingIconProposal,
    PendingIconProposalListResponse,
    PendingIconProposalSearchRequest,
    ProposeIconAction,
    RejectIconAction,
    SetIconAction,
)
from registry_api.signatures import (
    validate_signature_encoding,
    verify_canonical_payload_signature,
)
from registry_api.validation import normalize_asset_id

MAX_NEW_ICON_BYTES = 75_000
MAX_STORED_ICON_BYTES = 1_048_576
ICON_DIMENSIONS = (500, 500)


def decode_and_validate_new_icon(encoded: str) -> tuple[bytes, str]:
    image_data = _decode_canonical_base64(encoded)
    if len(image_data) >= MAX_NEW_ICON_BYTES:
        raise RegistryError(
            ErrorCode.INVALID_ICON,
            f"icon must be smaller than {MAX_NEW_ICON_BYTES} decoded bytes",
            {"decoded_bytes": len(image_data), "maximum_exclusive": MAX_NEW_ICON_BYTES},
        )
    image = _verified_png(image_data)
    if image.size != ICON_DIMENSIONS:
        raise RegistryError(
            ErrorCode.INVALID_ICON,
            "icon must be exactly 500x500 pixels",
            {"width": image.width, "height": image.height},
        )
    if "A" not in image.getbands():
        raise RegistryError(
            ErrorCode.INVALID_ICON, "icon PNG must contain an alpha channel"
        )
    return image_data, hashlib.sha256(image_data).hexdigest()


def decode_legacy_icon(encoded: str) -> tuple[bytes, str, list[str]]:
    image_data = _decode_canonical_base64(encoded)
    if len(image_data) > MAX_STORED_ICON_BYTES:
        raise RegistryError(
            ErrorCode.INVALID_ICON,
            f"legacy icon must not exceed {MAX_STORED_ICON_BYTES} decoded bytes",
        )
    image = _verified_png(image_data)
    deviations: list[str] = []
    if image.size != ICON_DIMENSIONS:
        deviations.append("dimensions")
    if len(image_data) >= MAX_NEW_ICON_BYTES:
        deviations.append("size")
    if "A" not in image.getbands():
        deviations.append("alpha")
    return image_data, hashlib.sha256(image_data).hexdigest(), deviations


def submit_icon_proposal(
    db: Session,
    *,
    asset_id: str,
    action: ProposeIconAction,
    icon: str,
    signature: str,
    now: datetime | None = None,
    freshness_window: timedelta = FRESHNESS_WINDOW,
) -> IconProposalResponse:
    validate_signature_encoding(signature)
    image_data = _validated_proposal_image(asset_id, action, icon)
    asset = _active_asset_for_icon_proposal(db, action.asset_id)
    normalized_action = action.model_dump(mode="json")
    retry = _existing_icon_proposal_response(
        db,
        asset=asset,
        nonce=action.nonce,
        normalized_action=normalized_action,
    )
    if retry is not None:
        return retry
    _ensure_prev_action_hash(
        asset, action.prev_action_hash, _latest_chain_action(db, asset)
    )
    _check_freshness(
        db, asset, action.timestamp, now=now, freshness_window=freshness_window
    )
    verify_canonical_payload_signature(
        asset.current_issuer_pubkey,
        signature,
        canonical_json_bytes(normalized_action),
    )
    _require_icon_proposal_slot(db, asset, action.icon_hash)
    action_row = new_action(
        asset,
        actor=Actor.ISSUER,
        operation=Operation.PROPOSE_ICON,
        payload=normalized_action,
        signature=signature,
        nonce=action.nonce,
        issuer_timestamp=action.timestamp,
        verified_pubkey=asset.current_issuer_pubkey,
        participates_in_hash_chain=True,
    )
    proposal = AssetIconProposal(
        asset_uuid=asset.asset_uuid,
        icon_hash=action.icon_hash,
        image_data=image_data,
        status=IconProposalStatus.PENDING,
        submission_method="v2_issuer_signature",
        proposed_by_action=action_row,
    )
    asset.updated_at = datetime.now(UTC)
    _commit_icon_proposal(db, asset, action_row, proposal)
    return _proposal_response("applied", proposal, action_row, asset.asset_id)


def _validated_proposal_image(
    asset_id: str,
    action: ProposeIconAction,
    icon: str,
) -> bytes:
    image_data, expected_hash = decode_and_validate_new_icon(icon)
    if expected_hash != action.icon_hash:
        raise RegistryError(
            ErrorCode.ICON_HASH_MISMATCH,
            "submitted icon_hash does not match the decoded image bytes",
            {
                "expected_icon_hash": expected_hash,
                "submitted_icon_hash": action.icon_hash,
            },
            status_code=409,
        )
    if action.asset_id != asset_id.lower():
        raise RegistryError(
            ErrorCode.ASSET_ID_MISMATCH,
            "URL asset_id does not match signed action asset_id",
            {"url_asset_id": asset_id, "action_asset_id": action.asset_id},
        )
    return image_data


def _active_asset_for_icon_proposal(db: Session, asset_id: str) -> Asset:
    asset = db.scalar(
        select(Asset).where(
            Asset.asset_id == asset_id,
            Asset.status == "active",
        )
    )
    if asset is None:
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "asset not found",
            {"asset_id": asset_id},
            status_code=404,
        )
    if (
        asset.contract_version == 0
        and asset.initial_issuer_pubkey_source != "migrated_legacy_record"
    ):
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "asset not found; legacy assets must be migrated before v2 issuer actions",
            {"asset_id": asset_id},
            status_code=404,
        )
    return asset


def _existing_icon_proposal_response(
    db: Session,
    *,
    asset: Asset,
    nonce: str,
    normalized_action: dict,
) -> IconProposalResponse | None:
    existing = db.scalar(
        select(Action).where(
            Action.asset_uuid == asset.asset_uuid,
            Action.actor == Actor.ISSUER,
            Action.nonce == nonce,
        )
    )
    if existing is None:
        return None
    if existing.action != normalized_action:
        raise RegistryError(
            ErrorCode.NONCE_CONFLICT,
            "nonce has already been used for a different issuer action",
            status_code=409,
        )
    proposal = db.scalar(
        select(AssetIconProposal).where(
            AssetIconProposal.proposed_by_action_uuid == existing.action_uuid
        )
    )
    if proposal is None:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "icon proposal state is missing",
            status_code=500,
        )
    return _proposal_response("idempotent_retry", proposal, existing, asset.asset_id)


def _require_icon_proposal_slot(
    db: Session,
    asset: Asset,
    icon_hash: str,
) -> None:
    pending = db.scalar(
        select(AssetIconProposal.icon_proposal_uuid).where(
            AssetIconProposal.asset_uuid == asset.asset_uuid,
            AssetIconProposal.status == IconProposalStatus.PENDING,
            AssetIconProposal.obsoleted_at.is_(None),
        )
    )
    if pending is not None:
        raise RegistryError(
            ErrorCode.ICON_PENDING_CONFLICT,
            "asset already has a pending icon proposal",
            {"asset_id": asset.asset_id},
            status_code=409,
        )
    current = current_icon_proposal(db, asset.asset_uuid)
    if current is not None and current.icon_hash == icon_hash:
        raise RegistryError(
            ErrorCode.NO_OP_ACTION,
            "proposed icon is already the current approved icon",
        )


def _commit_icon_proposal(
    db: Session,
    asset: Asset,
    action_row: Action,
    proposal: AssetIconProposal,
) -> None:
    try:
        db.add_all([action_row, proposal])
        db.flush()
        from registry_api.serialized_fragments import refresh_asset_serialized_fragments

        refresh_asset_serialized_fragments(db, asset)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        pending = db.scalar(
            select(AssetIconProposal.icon_proposal_uuid).where(
                AssetIconProposal.asset_uuid == asset.asset_uuid,
                AssetIconProposal.status == IconProposalStatus.PENDING,
                AssetIconProposal.obsoleted_at.is_(None),
            )
        )
        if pending is not None:
            raise RegistryError(
                ErrorCode.ICON_PENDING_CONFLICT,
                "asset already has a pending icon proposal",
                {"asset_id": asset.asset_id},
                status_code=409,
            ) from exc
        raise RegistryError(
            ErrorCode.NONCE_CONFLICT,
            "nonce has already been used for this asset",
            status_code=409,
        ) from exc
    except Exception:
        db.rollback()
        raise

    db.refresh(action_row)
    db.refresh(proposal)


def search_pending_icon_proposals(
    db: Session,
    *,
    payload: bytes,
    signature: str,
    now: datetime | None = None,
    freshness_window: timedelta = FRESHNESS_WINDOW,
) -> PendingIconProposalListResponse:
    query = _verify_pending_icon_search(
        db,
        payload=payload,
        signature=signature,
        now=now,
        freshness_window=freshness_window,
    )
    base_query = _reviewable_proposals_query()
    total_count = (
        db.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    )
    rows = db.execute(
        base_query.order_by(*_proposal_order(query.order))
        .offset((query.page - 1) * query.page_size)
        .limit(query.page_size)
    ).all()
    return PendingIconProposalListResponse(
        items=[
            PendingIconProposal(
                proposal_id=str(proposal.icon_proposal_uuid),
                asset_id=row_asset_id,
                icon_hash=proposal.icon_hash,
                icon=base64.b64encode(proposal.image_data or b"").decode("ascii"),
                proposed_at=proposal.proposed_at,
            )
            for proposal, row_asset_id in rows
        ],
        page=query.page,
        page_size=query.page_size,
        total_count=total_count,
        total_pages=math.ceil(total_count / query.page_size) if total_count else 0,
    )


def search_issuer_icon_proposals(
    db: Session,
    *,
    asset_id: str,
    payload: bytes,
    signature: str,
    now: datetime | None = None,
    freshness_window: timedelta = FRESHNESS_WINDOW,
) -> IssuerIconProposalListResponse:
    query = _verify_issuer_icon_search(
        asset_id=asset_id,
        payload=payload,
        signature=signature,
        now=now,
        freshness_window=freshness_window,
    )
    _require_asset_issuer_key(db, query)
    base_query = _issuer_proposals_query(query)
    total_count = (
        db.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    )
    rows = db.execute(
        base_query.order_by(*_proposal_order(query.order))
        .offset((query.page - 1) * query.page_size)
        .limit(query.page_size)
    ).all()
    return IssuerIconProposalListResponse(
        items=[
            IssuerIconProposal(
                proposal_id=str(proposal.icon_proposal_uuid),
                asset_id=row_asset_id,
                icon_hash=proposal.icon_hash,
                status=IconProposalStatus(proposal.status),
                icon=(
                    base64.b64encode(proposal.image_data).decode("ascii")
                    if proposal.image_data is not None
                    else None
                ),
                proposed_at=proposal.proposed_at,
                decided_at=proposal.decided_at,
                obsoleted_at=proposal.obsoleted_at,
            )
            for proposal, row_asset_id in rows
        ],
        page=query.page,
        page_size=query.page_size,
        total_count=total_count,
        total_pages=math.ceil(total_count / query.page_size) if total_count else 0,
    )


def _verify_pending_icon_search(
    db: Session,
    *,
    payload: bytes,
    signature: str,
    now: datetime | None,
    freshness_window: timedelta,
) -> PendingIconProposalSearchRequest:
    validate_signature_encoding(signature)
    parsed = require_canonical_json(payload)
    try:
        query = PendingIconProposalSearchRequest.model_validate(parsed)
    except ValidationError as exc:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "admin icon proposal search failed validation",
            {"errors": clean_pydantic_errors(exc)},
        ) from exc
    verify_canonical_payload_signature(
        query.actor_pubkey,
        signature,
        payload,
        failure_message="admin query signature verification failed",
    )
    actor = get_active_admin(db, query.actor_pubkey)
    _check_query_freshness(
        query.timestamp,
        now=now,
        freshness_window=freshness_window,
        actor_label="admin",
    )
    require_admin_permission(actor, "review_icons")
    return query


def _verify_issuer_icon_search(
    *,
    asset_id: str,
    payload: bytes,
    signature: str,
    now: datetime | None,
    freshness_window: timedelta,
) -> IssuerIconProposalSearchRequest:
    validate_signature_encoding(signature)
    parsed = require_canonical_json(payload)
    try:
        query = IssuerIconProposalSearchRequest.model_validate(parsed)
    except ValidationError as exc:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "issuer icon proposal search failed validation",
            {"errors": clean_pydantic_errors(exc)},
        ) from exc
    if query.asset_id != asset_id.lower():
        raise RegistryError(
            ErrorCode.ASSET_ID_MISMATCH,
            "URL asset_id does not match signed query asset_id",
            {"url_asset_id": asset_id, "query_asset_id": query.asset_id},
        )
    verify_canonical_payload_signature(
        query.actor_pubkey,
        signature,
        payload,
        failure_message="issuer query signature verification failed",
    )
    _check_query_freshness(
        query.timestamp,
        now=now,
        freshness_window=freshness_window,
        actor_label="issuer",
    )
    return query


def _check_query_freshness(
    timestamp: datetime,
    *,
    now: datetime | None,
    freshness_window: timedelta,
    actor_label: str,
) -> None:
    current_time = now or datetime.now(UTC)
    if abs(current_time - timestamp.astimezone(UTC)) > freshness_window:
        raise RegistryError(
            ErrorCode.STALE_TIMESTAMP,
            f"{actor_label} query timestamp is outside the accepted freshness window",
        )


def _reviewable_proposals_query() -> Select:
    return (
        select(AssetIconProposal, Asset.asset_id)
        .join(Asset, Asset.asset_uuid == AssetIconProposal.asset_uuid)
        .where(
            Asset.status == "active",
            AssetIconProposal.status == IconProposalStatus.PENDING,
            AssetIconProposal.obsoleted_at.is_(None),
        )
    )


def _issuer_proposals_query(
    query: IssuerIconProposalSearchRequest,
) -> Select:
    statement = (
        select(AssetIconProposal, Asset.asset_id)
        .join(Asset, Asset.asset_uuid == AssetIconProposal.asset_uuid)
        .join(
            Action,
            Action.action_uuid == AssetIconProposal.proposed_by_action_uuid,
        )
        .where(
            Asset.asset_id == query.asset_id,
            Action.actor == Actor.ISSUER,
            Action.verified_pubkey == query.actor_pubkey,
        )
    )
    if query.status is not None:
        statement = statement.where(AssetIconProposal.status == query.status)
    return statement


def _require_asset_issuer_key(
    db: Session,
    query: IssuerIconProposalSearchRequest,
) -> None:
    issuer_key = db.scalar(
        select(IssuerPubkeyHistory.issuer_pubkey_history_uuid)
        .join(Asset, Asset.asset_uuid == IssuerPubkeyHistory.asset_uuid)
        .where(
            Asset.asset_id == query.asset_id,
            IssuerPubkeyHistory.pubkey == query.actor_pubkey,
        )
        .limit(1)
    )
    if issuer_key is None:
        raise RegistryError(
            ErrorCode.FORBIDDEN,
            "signing key is not an issuer for this asset",
            status_code=403,
        )


def _proposal_order(order: Literal["asc", "desc"]) -> tuple:
    if order == "desc":
        return (
            AssetIconProposal.proposed_at.desc(),
            AssetIconProposal.icon_proposal_uuid.desc(),
        )
    return (
        AssetIconProposal.proposed_at.asc(),
        AssetIconProposal.icon_proposal_uuid.asc(),
    )


def decide_icon_proposal(
    db: Session,
    *,
    asset_id: str,
    payload: bytes,
    signature: str,
    now: datetime | None = None,
):
    verified, signed_action = _verify_icon_decision(
        db,
        asset_id=asset_id,
        payload=payload,
        signature=signature,
        now=now,
    )
    asset = _active_asset_for_icon_decision(db, signed_action.asset_id)
    existing_action = _validate_icon_decision_nonce(db, verified, signed_action.nonce)
    proposal = _lock_pending_icon_proposal(
        db,
        asset=asset,
        icon_hash=signed_action.icon_hash,
    )
    if existing_action is not None:
        raise _already_decided_error(asset.asset_id, proposal)
    _persist_icon_decision(
        db,
        asset=asset,
        proposal=proposal,
        verified=verified,
        signed_action=signed_action,
    )
    from registry_api.v2_assets import asset_response_from_row

    return asset_response_from_row(db, asset)


def set_admin_asset_icon(
    db: Session,
    *,
    asset_id: str,
    action: SetIconAction,
    icon: str,
    signature: str,
    now: datetime | None = None,
):
    validate_signature_encoding(signature)
    image_data = _validated_admin_icon_image(asset_id, action, icon)
    normalized_action = action.model_dump(mode="json")
    verified = verify_admin_asset_action(
        db,
        payload=canonical_json_bytes(normalized_action),
        signature=signature,
        now=now,
    )
    if not isinstance(verified.action, SetIconAction):
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "admin action must set_icon",
        )
    require_admin_permission(verified.actor, "manage_icons")
    asset = _active_asset_for_icon_decision(db, action.asset_id)
    existing_action = _validate_icon_decision_nonce(db, verified, action.nonce)
    if existing_action is not None:
        from registry_api.v2_assets import asset_response_from_row

        return asset_response_from_row(db, asset)
    _reject_current_icon_no_op(asset, action.icon_hash)
    proposal = _reusable_icon_proposal(
        db,
        asset=asset,
        icon_hash=action.icon_hash,
        image_data=image_data,
    )
    _persist_admin_icon_assignment(
        db,
        asset=asset,
        proposal=proposal,
        image_data=image_data,
        verified=verified,
        action=action,
    )
    from registry_api.v2_assets import asset_response_from_row

    return asset_response_from_row(db, asset)


def _validated_admin_icon_image(
    asset_id: str,
    action: SetIconAction,
    icon: str,
) -> bytes:
    image_data, expected_hash = decode_and_validate_new_icon(icon)
    if expected_hash != action.icon_hash:
        raise RegistryError(
            ErrorCode.ICON_HASH_MISMATCH,
            "submitted icon_hash does not match the decoded image bytes",
            {
                "expected_icon_hash": expected_hash,
                "submitted_icon_hash": action.icon_hash,
            },
            status_code=409,
        )
    if action.asset_id != asset_id.lower():
        raise RegistryError(
            ErrorCode.ASSET_ID_MISMATCH,
            "URL asset_id does not match signed action asset_id",
            {"url_asset_id": asset_id, "action_asset_id": action.asset_id},
        )
    return image_data


def _reject_current_icon_no_op(asset: Asset, icon_hash: str) -> None:
    if asset.icon is not None and asset.icon.icon_hash == icon_hash:
        raise RegistryError(
            ErrorCode.NO_OP_ACTION,
            "submitted icon is already the asset's active icon",
        )


def _reusable_icon_proposal(
    db: Session,
    *,
    asset: Asset,
    icon_hash: str,
    image_data: bytes,
) -> AssetIconProposal | None:
    proposal = db.scalar(
        select(AssetIconProposal)
        .where(
            AssetIconProposal.asset_uuid == asset.asset_uuid,
            AssetIconProposal.icon_hash == icon_hash,
            AssetIconProposal.obsoleted_at.is_(None),
            AssetIconProposal.image_data.is_not(None),
            AssetIconProposal.status.in_(
                [IconProposalStatus.APPROVED, IconProposalStatus.PENDING]
            ),
        )
        .order_by(
            (AssetIconProposal.status == IconProposalStatus.PENDING).desc(),
            AssetIconProposal.proposed_at.desc(),
        )
        .limit(1)
    )
    if proposal is not None and proposal.image_data != image_data:
        raise RegistryError(
            ErrorCode.ICON_HASH_MISMATCH,
            "stored icon bytes do not match the submitted bytes for this hash",
            {"icon_hash": icon_hash},
            status_code=409,
        )
    return proposal


def _persist_admin_icon_assignment(
    db: Session,
    *,
    asset: Asset,
    proposal: AssetIconProposal | None,
    image_data: bytes,
    verified: VerifiedAdminAction,
    action: SetIconAction,
) -> None:
    action_row = new_action(
        asset,
        actor=Actor.ADMIN,
        operation=Operation.SET_ICON,
        payload=verified.parsed,
        signature=verified.signature,
        nonce=action.nonce,
        issuer_timestamp=action.timestamp,
        verified_pubkey=verified.actor_pubkey,
        admin_id=str(verified.actor.admin_uuid),
    )
    annotations = _get_or_create_annotations(db, asset)
    assigned_proposal = proposal or AssetIconProposal(
        asset_uuid=asset.asset_uuid,
        icon_hash=action.icon_hash,
        image_data=image_data,
        status=IconProposalStatus.APPROVED,
        submission_method="admin_upload",
        proposed_by_action=action_row,
        decided_by_action=action_row,
        decided_at=datetime.now(UTC),
    )
    try:
        db.add(action_row)
        if proposal is None:
            db.add(assigned_proposal)
        db.flush()
        if assigned_proposal.status == IconProposalStatus.PENDING:
            assigned_proposal.status = IconProposalStatus.APPROVED
            assigned_proposal.decided_by_action_uuid = action_row.action_uuid
            assigned_proposal.decided_at = datetime.now(UTC)
        asset.icon = assigned_proposal
        _mark_admin_asset_changed(asset, annotations, action_row, verified)
        db.flush()
        from registry_api.serialized_fragments import refresh_asset_serialized_fragments

        refresh_asset_serialized_fragments(db, asset)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(asset)


def _verify_icon_decision(
    db: Session,
    *,
    asset_id: str,
    payload: bytes,
    signature: str,
    now: datetime | None,
) -> tuple[VerifiedAdminAction, ApproveIconAction | RejectIconAction]:
    verified = verify_admin_asset_action(
        db,
        payload=payload,
        signature=signature,
        now=now,
    )
    signed_action = verified.action
    if not isinstance(signed_action, (ApproveIconAction, RejectIconAction)):
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "admin action must approve_icon or reject_icon",
        )
    if signed_action.asset_id != asset_id.lower():
        raise RegistryError(
            ErrorCode.ASSET_ID_MISMATCH,
            "URL asset_id does not match signed action asset_id",
            {"url_asset_id": asset_id, "action_asset_id": signed_action.asset_id},
        )
    require_admin_permission(verified.actor, "review_icons")
    return verified, signed_action


def _active_asset_for_icon_decision(db: Session, asset_id: str) -> Asset:
    asset = db.scalar(
        select(Asset).where(
            Asset.asset_id == asset_id,
            Asset.status == "active",
        )
    )
    if asset is None:
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "asset not found",
            {"asset_id": asset_id},
            status_code=404,
        )
    return asset


def _validate_icon_decision_nonce(
    db: Session,
    verified: VerifiedAdminAction,
    nonce: str,
) -> Action | None:
    existing_action = _existing_admin_asset_action(db, verified, nonce)
    if existing_action is not None and existing_action.action != verified.parsed:
        raise RegistryError(
            ErrorCode.NONCE_CONFLICT,
            "nonce has already been used for a different admin action",
            status_code=409,
        )
    return existing_action


def _lock_pending_icon_proposal(
    db: Session,
    *,
    asset: Asset,
    icon_hash: str,
) -> AssetIconProposal:
    proposal = db.scalar(
        select(AssetIconProposal)
        .where(
            AssetIconProposal.asset_uuid == asset.asset_uuid,
            AssetIconProposal.icon_hash == icon_hash,
            AssetIconProposal.status == IconProposalStatus.PENDING,
            AssetIconProposal.obsoleted_at.is_(None),
        )
        .order_by(AssetIconProposal.proposed_at.desc())
        .with_for_update()
        .limit(1)
    )
    if proposal is not None:
        return proposal
    decided = db.scalar(
        select(AssetIconProposal)
        .where(
            AssetIconProposal.asset_uuid == asset.asset_uuid,
            AssetIconProposal.icon_hash == icon_hash,
        )
        .order_by(AssetIconProposal.proposed_at.desc())
        .limit(1)
    )
    if decided is not None and decided.status != IconProposalStatus.PENDING:
        raise _already_decided_error(asset.asset_id, decided)
    raise RegistryError(
        ErrorCode.ICON_PROPOSAL_NOT_FOUND,
        "pending icon proposal not found",
        {"asset_id": asset.asset_id, "icon_hash": icon_hash},
        status_code=404,
    )


def _persist_icon_decision(
    db: Session,
    *,
    asset: Asset,
    proposal: AssetIconProposal,
    verified: VerifiedAdminAction,
    signed_action: ApproveIconAction | RejectIconAction,
) -> None:
    action_row = new_action(
        asset,
        actor=Actor.ADMIN,
        operation=signed_action.operation,
        payload=verified.parsed,
        signature=verified.signature,
        nonce=signed_action.nonce,
        issuer_timestamp=signed_action.timestamp,
        verified_pubkey=verified.actor_pubkey,
        admin_id=str(verified.actor.admin_uuid),
    )
    annotations = _get_or_create_annotations(db, asset)
    try:
        db.add(action_row)
        db.flush()
        _apply_icon_decision(asset, proposal, signed_action, action_row)
        _mark_admin_asset_changed(asset, annotations, action_row, verified)
        db.flush()
        from registry_api.serialized_fragments import refresh_asset_serialized_fragments

        refresh_asset_serialized_fragments(db, asset)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(asset)


def _apply_icon_decision(
    asset: Asset,
    proposal: AssetIconProposal,
    signed_action: ApproveIconAction | RejectIconAction,
    action_row: Action,
) -> None:
    if isinstance(signed_action, ApproveIconAction):
        proposal.status = IconProposalStatus.APPROVED
        asset.icon = proposal
    else:
        proposal.status = IconProposalStatus.REJECTED
        proposal.image_data = None
    proposal.decided_by_action_uuid = action_row.action_uuid
    proposal.decided_at = datetime.now(UTC)


def obsolete_asset_icon_proposals(
    db: Session,
    asset: Asset,
    action: Action,
) -> None:
    obsoleted_at = datetime.now(UTC)
    asset.icon = None
    db.execute(
        update(AssetIconProposal)
        .where(
            AssetIconProposal.asset_uuid == asset.asset_uuid,
            AssetIconProposal.obsoleted_at.is_(None),
        )
        .values(
            obsoleted_at=obsoleted_at,
            obsoleted_by_action_uuid=action.action_uuid,
        )
    )


def current_icon_proposal(
    db: Session, asset_uuid: uuid.UUID
) -> AssetIconProposal | None:
    return db.scalar(
        select(AssetIconProposal)
        .join(
            Asset,
            Asset.active_icon_proposal_uuid == AssetIconProposal.icon_proposal_uuid,
        )
        .where(
            Asset.asset_uuid == asset_uuid,
            AssetIconProposal.status == IconProposalStatus.APPROVED,
            AssetIconProposal.obsoleted_at.is_(None),
            AssetIconProposal.image_data.is_not(None),
        )
    )


def _normalize_published_icon_asset_id(asset_id: str) -> str:
    try:
        return normalize_asset_id(asset_id)
    except ValueError as exc:
        raise RegistryError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc


def _current_published_icon_query(normalized_asset_id: str) -> Select:
    return (
        select(AssetIconProposal)
        .join(
            Asset,
            Asset.active_icon_proposal_uuid == AssetIconProposal.icon_proposal_uuid,
        )
        .where(
            Asset.asset_id == normalized_asset_id,
            Asset.status == "active",
            AssetIconProposal.status == IconProposalStatus.APPROVED,
            AssetIconProposal.obsoleted_at.is_(None),
            AssetIconProposal.image_data.is_not(None),
        )
    )


def _published_icon_by_hash_query(
    normalized_asset_id: str, normalized_hash: str
) -> Select:
    return (
        select(AssetIconProposal)
        .join(Asset, Asset.asset_uuid == AssetIconProposal.asset_uuid)
        .where(
            Asset.asset_id == normalized_asset_id,
            AssetIconProposal.icon_hash == normalized_hash,
            AssetIconProposal.status == IconProposalStatus.APPROVED,
            AssetIconProposal.image_data.is_not(None),
        )
        .order_by(AssetIconProposal.proposed_at.desc())
        .limit(1)
    )


def _published_icon_not_found(
    normalized_asset_id: str, normalized_hash: str | None = None
) -> RegistryError:
    details = {"asset_id": normalized_asset_id}
    if normalized_hash is not None:
        details["icon_hash"] = normalized_hash
    return RegistryError(
        ErrorCode.ICON_NOT_FOUND,
        "published icon not found",
        details,
        status_code=404,
    )


def published_icon_for_asset(
    db: Session,
    asset_id: str,
    *,
    include_image_data: bool,
) -> AssetIconProposal:
    normalized_asset_id = _normalize_published_icon_asset_id(asset_id)
    query = _current_published_icon_query(normalized_asset_id)
    if not include_image_data:
        query = query.options(load_only(AssetIconProposal.icon_hash))
    proposal = db.scalar(query)
    if proposal is None:
        raise _published_icon_not_found(normalized_asset_id)
    return proposal


def published_icon_for_asset_by_hash(
    db: Session, asset_id: str, icon_hash: str
) -> AssetIconProposal:
    normalized_asset_id = _normalize_published_icon_asset_id(asset_id)
    normalized_hash = icon_hash.lower()
    proposal = db.scalar(
        _published_icon_by_hash_query(normalized_asset_id, normalized_hash)
    )
    if proposal is None:
        raise _published_icon_not_found(normalized_asset_id, normalized_hash)
    return proposal


def require_published_icon_for_asset_by_hash(
    db: Session, asset_id: str, icon_hash: str
) -> None:
    normalized_asset_id = _normalize_published_icon_asset_id(asset_id)
    normalized_hash = icon_hash.lower()
    published_hash = db.scalar(
        _published_icon_by_hash_query(
            normalized_asset_id, normalized_hash
        ).with_only_columns(AssetIconProposal.icon_hash)
    )
    if published_hash is None:
        raise _published_icon_not_found(normalized_asset_id, normalized_hash)


def icon_base64_from_asset(asset: Asset) -> str | None:
    if asset.icon is None or asset.icon.image_data is None:
        return None
    return base64.b64encode(asset.icon.image_data).decode("ascii")


def icon_map(db: Session) -> dict[str, str]:
    rows = db.execute(
        select(Asset.asset_id, AssetIconProposal.image_data)
        .join(
            AssetIconProposal,
            AssetIconProposal.icon_proposal_uuid == Asset.active_icon_proposal_uuid,
        )
        .where(
            Asset.status == "active",
            AssetIconProposal.status == IconProposalStatus.APPROVED,
            AssetIconProposal.obsoleted_at.is_(None),
            AssetIconProposal.image_data.is_not(None),
        )
        .order_by(Asset.asset_id.asc())
    )
    return {
        asset_id: base64.b64encode(image_data).decode("ascii")
        for asset_id, image_data in rows
    }


@lru_cache(maxsize=1)
def liquid_mainnet_policy_asset_icon_base64() -> str:
    """Load and validate the packaged compatibility icon once per process."""
    image_data = files("registry_api").joinpath("data", "lbtc-icon.png").read_bytes()
    if len(image_data) > MAX_STORED_ICON_BYTES:
        raise RuntimeError("bundled Liquid policy asset icon is too large")
    try:
        image = _verified_png(image_data)
    except RegistryError as exc:
        raise RuntimeError("bundled Liquid policy asset icon is invalid") from exc
    image.close()
    return base64.b64encode(image_data).decode("ascii")


def stream_icon_map_bytes(
    *, fallback_icons: Mapping[str, str] | None = None
) -> Iterator[bytes]:
    """Stream approved icons plus ordered fallbacks, preferring database rows."""
    from registry_api.db import SessionLocal

    with SessionLocal() as db:
        rows = db.execute(
            select(Asset.asset_id, AssetIconProposal.image_data)
            .join(
                AssetIconProposal,
                AssetIconProposal.icon_proposal_uuid == Asset.active_icon_proposal_uuid,
            )
            .where(
                Asset.status == "active",
                AssetIconProposal.status == IconProposalStatus.APPROVED,
                AssetIconProposal.obsoleted_at.is_(None),
                AssetIconProposal.image_data.is_not(None),
            )
            .order_by(Asset.asset_id.asc())
        ).yield_per(100)
        fallback_items = iter(sorted((fallback_icons or {}).items()))
        fallback_item = next(fallback_items, None)
        yield b"{"
        first = True
        for asset_id, image_data in rows:
            while fallback_item is not None and fallback_item[0] < asset_id:
                yield from _stream_icon_map_entry(
                    fallback_item[0], fallback_item[1], first=first
                )
                first = False
                fallback_item = next(fallback_items, None)
            if fallback_item is not None and fallback_item[0] == asset_id:
                fallback_item = next(fallback_items, None)
            yield from _stream_icon_map_entry(
                asset_id,
                base64.b64encode(image_data).decode("ascii"),
                first=first,
            )
            first = False
        while fallback_item is not None:
            yield from _stream_icon_map_entry(
                fallback_item[0], fallback_item[1], first=first
            )
            first = False
            fallback_item = next(fallback_items, None)
        yield b"}"


def liquid_mainnet_policy_asset_icon_fallback() -> dict[str, str]:
    """Return the removable /icons.json fallback for Liquid's policy asset."""
    return {LIQUID_MAINNET_POLICY_ASSET_ID: liquid_mainnet_policy_asset_icon_base64()}


def _stream_icon_map_entry(
    asset_id: str, encoded: str, *, first: bool
) -> Iterator[bytes]:
    if not first:
        yield b","
    yield to_json(asset_id)
    yield b":"
    yield to_json(encoded)


def _decode_canonical_base64(encoded: str) -> bytes:
    try:
        image_data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RegistryError(
            ErrorCode.INVALID_ICON, "icon must be canonical RFC 4648 Base64"
        ) from exc
    if base64.b64encode(image_data).decode("ascii") != encoded:
        raise RegistryError(
            ErrorCode.INVALID_ICON, "icon must be canonical RFC 4648 Base64"
        )
    return image_data


def _verified_png(image_data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(image_data)) as candidate:
            if candidate.format != "PNG":
                raise RegistryError(ErrorCode.INVALID_ICON, "icon must be a PNG image")
            candidate.verify()
        image = Image.open(io.BytesIO(image_data))
        image.load()
        return image
    except RegistryError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RegistryError(
            ErrorCode.INVALID_ICON, "icon must be a valid PNG image"
        ) from exc


def _proposal_response(
    response_status: Literal["applied", "idempotent_retry"],
    proposal: AssetIconProposal,
    action: Action,
    asset_id: str,
) -> IconProposalResponse:
    return IconProposalResponse(
        status=response_status,
        proposal=IconProposalSummary(
            proposal_id=str(proposal.icon_proposal_uuid),
            asset_id=asset_id,
            icon_hash=proposal.icon_hash,
            status=IconProposalStatus(proposal.status),
            proposed_at=proposal.proposed_at,
            decided_at=proposal.decided_at,
            obsoleted_at=proposal.obsoleted_at,
        ),
        audit_entry=audit_entry_from_row(action),
    )


def _already_decided_error(asset_id: str, proposal: AssetIconProposal) -> RegistryError:
    return RegistryError(
        ErrorCode.ICON_PROPOSAL_ALREADY_DECIDED,
        "an approval decision has already been made for this icon proposal",
        {
            "asset_id": asset_id,
            "icon_hash": proposal.icon_hash,
            "status": proposal.status,
            "proposal_id": str(proposal.icon_proposal_uuid),
        },
        status_code=409,
    )
