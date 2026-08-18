from datetime import datetime
from typing import Literal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from registry_api.constants import Actor
from registry_api.errors import ErrorCode, RegistryError
from registry_api.models import Action, AdminAction, Asset
from registry_api.schemas import AuditEntry, AuditLogResponse
from registry_api.validation import normalize_asset_id

AuditOrder = Literal["asc", "desc"]
MAX_AUDIT_ID = 9_223_372_036_854_775_807


def get_asset_audit_log(
    db: Session,
    *,
    asset_id: str,
    since_audit_id: int = 0,
    limit: int = 100,
    order: AuditOrder = "asc",
) -> AuditLogResponse:
    normalized_asset_id = _normalize_asset_id(asset_id)
    if db.scalar(select(Asset.asset_uuid).where(Asset.asset_id == normalized_asset_id).limit(1)) is None:
        raise RegistryError(ErrorCode.ASSET_NOT_FOUND, "asset not found", {"asset_id": asset_id}, status_code=404)
    return search_audit_log(
        db,
        asset_id=normalized_asset_id,
        since_audit_id=since_audit_id,
        limit=limit,
        order=order,
    )


def search_audit_log(
    db: Session,
    *,
    since_audit_id: int = 0,
    limit: int = 100,
    asset_id: str | None = None,
    operation: str | None = None,
    actor: Literal[Actor.ISSUER, Actor.ADMIN, Actor.SYSTEM] | None = None,  # pyright: ignore[reportInvalidTypeForm]
    from_server_received_at: datetime | None = None,
    to_server_received_at: datetime | None = None,
    order: AuditOrder = "asc",
) -> AuditLogResponse:
    if since_audit_id < 0:
        raise RegistryError(ErrorCode.VALIDATION_ERROR, "since_audit_id must be greater than or equal to 0")
    if since_audit_id > MAX_AUDIT_ID:
        raise RegistryError(ErrorCode.VALIDATION_ERROR, f"since_audit_id must be less than or equal to {MAX_AUDIT_ID}")
    if limit < 1 or limit > 1000:
        raise RegistryError(ErrorCode.VALIDATION_ERROR, "limit must be between 1 and 1000")
    if order not in {"asc", "desc"}:
        raise RegistryError(ErrorCode.VALIDATION_ERROR, "order must be asc or desc", {"order": order})

    actions_query = _filtered_actions_query(
        since_audit_id=since_audit_id,
        asset_id=asset_id,
        operation=operation,
        actor=actor,
        from_server_received_at=from_server_received_at,
        to_server_received_at=to_server_received_at,
    )
    admin_actions_query = _filtered_admin_actions_query(
        since_audit_id=since_audit_id,
        asset_id=asset_id,
        operation=operation,
        actor=actor,
        from_server_received_at=from_server_received_at,
        to_server_received_at=to_server_received_at,
    )
    if order == "desc":
        actions_query = actions_query.order_by(Action.audit_sequence.desc())
        if admin_actions_query is not None:
            admin_actions_query = admin_actions_query.order_by(AdminAction.audit_sequence.desc())
    else:
        actions_query = actions_query.order_by(Action.audit_sequence.asc())
        if admin_actions_query is not None:
            admin_actions_query = admin_actions_query.order_by(AdminAction.audit_sequence.asc())

    action_rows = db.scalars(actions_query.limit(limit)).all()
    admin_action_rows = db.scalars(admin_actions_query.limit(limit)).all() if admin_actions_query is not None else []
    rows = sorted(
        [*action_rows, *admin_action_rows],
        key=lambda row: row.audit_sequence,
        reverse=order == "desc",
    )[:limit]
    return AuditLogResponse(
        items=[audit_entry_from_any_row(row) for row in rows],
        next_since_audit_id=max((row.audit_sequence for row in rows), default=None),
    )


def audit_entry_from_any_row(action: Action | AdminAction) -> AuditEntry:
    if isinstance(action, AdminAction):
        return audit_entry_from_admin_row(action)
    return audit_entry_from_row(action)


def audit_entry_from_row(action: Action) -> AuditEntry:
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


def audit_entry_from_admin_row(action: AdminAction) -> AuditEntry:
    return AuditEntry(
        audit_id=action.audit_sequence,
        server_received_at=action.server_received_at,
        actor=Actor.ADMIN,
        verified_pubkey=action.actor_pubkey,
        admin_id=str(action.actor_admin_uuid),
        action=action.action,
        signature=action.signature,
    )


def _filtered_actions_query(
    *,
    since_audit_id: int,
    asset_id: str | None,
    operation: str | None,
    actor: Literal[Actor.ISSUER, Actor.ADMIN, Actor.SYSTEM] | None,  # pyright: ignore[reportInvalidTypeForm]
    from_server_received_at: datetime | None,
    to_server_received_at: datetime | None,
) -> Select[tuple[Action]]:
    query = select(Action).where(Action.audit_sequence > since_audit_id)
    if asset_id is not None:
        query = query.where(Action.asset_chain_id == _normalize_asset_id(asset_id))
    if operation is not None:
        query = query.where(Action.operation == operation)
    if actor is not None:
        query = query.where(Action.actor == actor)
    if from_server_received_at is not None:
        query = query.where(Action.server_received_at >= from_server_received_at)
    if to_server_received_at is not None:
        query = query.where(Action.server_received_at <= to_server_received_at)
    return query


def _filtered_admin_actions_query(
    *,
    since_audit_id: int,
    asset_id: str | None,
    operation: str | None,
    actor: Literal[Actor.ISSUER, Actor.ADMIN, Actor.SYSTEM] | None,  # pyright: ignore[reportInvalidTypeForm]
    from_server_received_at: datetime | None,
    to_server_received_at: datetime | None,
) -> Select[tuple[AdminAction]] | None:
    if asset_id is not None or actor in {Actor.ISSUER, Actor.SYSTEM}:
        return None
    query = select(AdminAction).where(AdminAction.audit_sequence > since_audit_id)
    if operation is not None:
        query = query.where(AdminAction.operation == operation)
    if from_server_received_at is not None:
        query = query.where(AdminAction.server_received_at >= from_server_received_at)
    if to_server_received_at is not None:
        query = query.where(AdminAction.server_received_at <= to_server_received_at)
    return query


def _normalize_asset_id(asset_id: str) -> str:
    try:
        return normalize_asset_id(asset_id)
    except ValueError as exc:
        raise RegistryError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
