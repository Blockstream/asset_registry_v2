from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import wallycore as wally
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from registry_api.canonical_json import require_canonical_json
from registry_api.constants import ADMIN_LIFECYCLE_OPERATIONS, Actor
from registry_api.errors import ErrorCode, RegistryError, clean_pydantic_errors
from registry_api.models import Action, AdminAction, AdminKey, AdminPermission
from registry_api.schemas import (
    AddAdminAction,
    AdminActionResponse,
    AdminLifecycleAction,
    AuditEntry,
    RemoveAdminAction,
    SignedAdminAssetAction,
    UpdateAdminNameAction,
    UpdateAdminPermissionsAction,
)
from registry_api.signatures import (
    validate_signature_encoding,
    verify_canonical_payload_signature,
)
from registry_api.validation import normalize_pubkey

ADMIN_LIFECYCLE_ACTION_ADAPTER = TypeAdapter(AdminLifecycleAction)
ADMIN_ASSET_ACTION_ADAPTER = TypeAdapter(SignedAdminAssetAction)
FRESHNESS_WINDOW = timedelta(minutes=5)


@dataclass(frozen=True)
class VerifiedAdminAction:
    action: AdminLifecycleAction | SignedAdminAssetAction
    parsed: dict
    payload: bytes
    signature: str
    actor: AdminKey
    actor_pubkey: str


def bootstrap_genesis_admin(db: Session, genesis_pubkey: str | None) -> AdminKey | None:
    if genesis_pubkey is None:
        return None
    pubkey = _validate_admin_pubkey(genesis_pubkey)
    existing_admin_count = db.scalar(select(func.count()).select_from(AdminKey))
    if existing_admin_count:
        return None

    genesis = AdminKey(pubkey=pubkey, friendly_name="Genesis Admin", status="active")
    db.add(genesis)
    db.flush()
    db.add(AdminPermission(admin_uuid=genesis.admin_uuid, permission="root"))
    db.commit()
    db.refresh(genesis)
    return genesis


def submit_admin_lifecycle_action(
    db: Session,
    *,
    payload: bytes,
    signature: str,
    now: datetime | None = None,
    freshness_window: timedelta = FRESHNESS_WINDOW,
) -> AdminActionResponse:
    verified = verify_admin_lifecycle_action(
        db,
        payload=payload,
        signature=signature,
        now=now,
        freshness_window=freshness_window,
    )
    existing = _existing_admin_action(db, verified.actor, verified.action.nonce)
    if existing is not None:
        if existing.action == verified.parsed:
            return AdminActionResponse(
                status="idempotent_retry", audit_entry=admin_audit_entry(existing)
            )
        raise RegistryError(
            ErrorCode.NONCE_CONFLICT,
            "nonce has already been used for a different admin action",
            status_code=409,
        )
    _reject_no_op_lifecycle_action(db, verified.action)

    row = AdminAction(
        actor_admin_uuid=verified.actor.admin_uuid,
        actor_pubkey=verified.actor_pubkey,
        operation=verified.action.operation,
        action=verified.parsed,
        signature=signature,
        nonce=verified.action.nonce,
        admin_timestamp=verified.action.timestamp,
    )
    try:
        db.add(row)
        db.flush()
        _apply_lifecycle_action(db, verified.actor, verified.action, row)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RegistryError(
            ErrorCode.NONCE_CONFLICT,
            "nonce has already been used for this admin",
            status_code=409,
        ) from exc
    except Exception:
        db.rollback()
        raise

    db.refresh(row)
    return AdminActionResponse(status="applied", audit_entry=admin_audit_entry(row))


def verify_admin_lifecycle_action(
    db: Session,
    *,
    payload: bytes,
    signature: str,
    now: datetime | None = None,
    freshness_window: timedelta = FRESHNESS_WINDOW,
) -> VerifiedAdminAction:
    validate_signature_encoding(signature)
    parsed = require_canonical_json(payload)
    try:
        action = ADMIN_LIFECYCLE_ACTION_ADAPTER.validate_python(parsed)
    except ValidationError as exc:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "admin action failed validation",
            {"errors": clean_pydantic_errors(exc)},
        ) from exc
    return _verify_admin_action(
        db,
        action=action,
        parsed=parsed,
        payload=payload,
        signature=signature,
        now=now,
        freshness_window=freshness_window,
    )


def verify_admin_asset_action(
    db: Session,
    *,
    payload: bytes,
    signature: str,
    now: datetime | None = None,
    freshness_window: timedelta = FRESHNESS_WINDOW,
) -> VerifiedAdminAction:
    validate_signature_encoding(signature)
    parsed = require_canonical_json(payload)
    try:
        action = ADMIN_ASSET_ACTION_ADAPTER.validate_python(parsed)
    except ValidationError as exc:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "admin asset action failed validation",
            {"errors": clean_pydantic_errors(exc)},
        ) from exc
    return _verify_admin_action(
        db,
        action=action,
        parsed=parsed,
        payload=payload,
        signature=signature,
        now=now,
        freshness_window=freshness_window,
    )


def require_admin_permission(actor: AdminKey, permission: str) -> None:
    permissions = {row.permission for row in actor.permissions}
    if "root" in permissions or permission in permissions:
        return
    raise RegistryError(
        ErrorCode.FORBIDDEN,
        f"admin permission is required: {permission}",
        status_code=403,
    )


def get_active_admin(db: Session, pubkey: str) -> AdminKey:
    actor = db.scalar(
        select(AdminKey).where(
            AdminKey.pubkey == pubkey,
            AdminKey.status == "active",
        )
    )
    if actor is None:
        raise RegistryError(
            ErrorCode.FORBIDDEN,
            "signing admin key is not active",
            status_code=403,
        )
    return actor


def admin_audit_entry(row: AdminAction) -> AuditEntry:
    return AuditEntry(
        audit_id=row.audit_sequence,
        server_received_at=row.server_received_at,
        actor=Actor.ADMIN,
        verified_pubkey=row.actor_pubkey,
        admin_id=str(row.actor_admin_uuid),
        action=row.action,
        signature=row.signature,
    )


def _verify_admin_action(
    db: Session,
    *,
    action: AdminLifecycleAction | SignedAdminAssetAction,
    parsed: dict,
    payload: bytes,
    signature: str,
    now: datetime | None,
    freshness_window: timedelta,
) -> VerifiedAdminAction:
    actor_pubkey = action.actor_pubkey
    verify_canonical_payload_signature(
        actor_pubkey,
        signature,
        payload,
        failure_message="admin action signature verification failed",
    )
    actor = get_active_admin(db, actor_pubkey)
    if _existing_scoped_admin_action(db, actor, actor_pubkey, action) is None:
        _check_admin_freshness(
            db,
            actor,
            actor_pubkey,
            action.timestamp,
            now=now,
            freshness_window=freshness_window,
        )
    return VerifiedAdminAction(
        action=action,
        parsed=parsed,
        payload=payload,
        signature=signature,
        actor=actor,
        actor_pubkey=actor_pubkey,
    )


def _check_admin_freshness(
    db: Session,
    actor: AdminKey,
    actor_pubkey: str,
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
            "admin action timestamp is outside the accepted freshness window",
        )

    previous_lifecycle = db.scalar(
        select(AdminAction.admin_timestamp)
        .where(AdminAction.actor_admin_uuid == actor.admin_uuid)
        .order_by(AdminAction.admin_timestamp.desc())
        .limit(1)
    )
    previous_asset = db.scalar(
        select(Action.issuer_timestamp)
        .where(
            Action.actor == Actor.ADMIN,
            Action.verified_pubkey == actor_pubkey,
            Action.issuer_timestamp.is_not(None),
        )
        .order_by(Action.issuer_timestamp.desc())
        .limit(1)
    )
    previous_values = [
        value.astimezone(UTC)
        for value in (previous_lifecycle, previous_asset)
        if value is not None
    ]
    previous = max(previous_values, default=None)
    if previous is not None and timestamp_utc < previous:
        raise RegistryError(
            ErrorCode.STALE_TIMESTAMP,
            "admin action timestamp is older than the latest accepted admin action",
        )


def _apply_lifecycle_action(
    db: Session, actor: AdminKey, action: AdminLifecycleAction, row: AdminAction
) -> None:
    if isinstance(action, AddAdminAction):
        _apply_add_admin(db, actor, action, row)
        return
    if isinstance(action, UpdateAdminPermissionsAction):
        _apply_update_permissions(db, actor, action, row)
        return
    if isinstance(action, UpdateAdminNameAction):
        require_admin_permission(actor, "manage_admins")
        target = _require_admin(db, action.admin_pubkey)
        target.friendly_name = action.friendly_name
        return
    if isinstance(action, RemoveAdminAction):
        _apply_remove_admin(db, actor, action, row)
        return
    raise RegistryError(
        ErrorCode.UNSUPPORTED_OPERATION,
        "admin action operation is not supported",
        {"operation": action.operation},
    )


def _reject_no_op_lifecycle_action(db: Session, action: AdminLifecycleAction) -> None:
    if isinstance(action, AddAdminAction):
        target = db.scalar(
            select(AdminKey).where(AdminKey.pubkey == action.admin_pubkey)
        )
        if target is not None and target.status != "removed":
            raise _admin_conflict(action.admin_pubkey)
        return
    if isinstance(action, UpdateAdminPermissionsAction):
        target = _require_admin(db, action.admin_pubkey)
        current_permissions = {
            permission.permission for permission in target.permissions
        }
        if target.status == "active" and current_permissions == set(action.permissions):
            raise RegistryError(
                ErrorCode.NO_OP_ACTION, "action would not change registry state"
            )
        return
    if isinstance(action, UpdateAdminNameAction):
        target = _require_admin(db, action.admin_pubkey)
        if target.status == "active" and target.friendly_name == action.friendly_name:
            raise RegistryError(
                ErrorCode.NO_OP_ACTION, "action would not change registry state"
            )
        return
    if isinstance(action, RemoveAdminAction):
        target = db.scalar(
            select(AdminKey).where(AdminKey.pubkey == action.admin_pubkey)
        )
        if target is None or target.status == "removed":
            raise RegistryError(
                ErrorCode.NO_OP_ACTION, "action would not change registry state"
            )


def _apply_add_admin(
    db: Session, actor: AdminKey, action: AddAdminAction, row: AdminAction
) -> None:
    _require_management_permission(actor, action.permissions)
    target = db.scalar(select(AdminKey).where(AdminKey.pubkey == action.admin_pubkey))
    if target is not None and target.status != "removed":
        raise _admin_conflict(action.admin_pubkey)
    if target is None:
        target = AdminKey(
            pubkey=action.admin_pubkey,
            friendly_name=action.friendly_name,
            status="active",
            created_by_admin_action_uuid=row.admin_action_uuid,
        )
        db.add(target)
        db.flush()
    else:
        target.status = "active"
        target.friendly_name = action.friendly_name
        target.removed_by_admin_action_uuid = None
    _replace_permissions(db, target, action.permissions)


def _apply_update_permissions(
    db: Session,
    actor: AdminKey,
    action: UpdateAdminPermissionsAction,
    row: AdminAction,
) -> None:
    target = _require_admin(db, action.admin_pubkey)
    current_permissions = {permission.permission for permission in target.permissions}
    _require_management_permission(
        actor, list(current_permissions | set(action.permissions))
    )
    if "root" in current_permissions and "root" not in set(action.permissions):
        _ensure_another_active_root(db, target)
    _replace_permissions(db, target, action.permissions)


def _apply_remove_admin(
    db: Session, actor: AdminKey, action: RemoveAdminAction, row: AdminAction
) -> None:
    target = _require_admin(db, action.admin_pubkey)
    target_permissions = {permission.permission for permission in target.permissions}
    _require_management_permission(actor, list(target_permissions))
    if "root" in target_permissions:
        _ensure_another_active_root(db, target)
    target.status = "removed"
    target.removed_by_admin_action_uuid = row.admin_action_uuid


def _require_management_permission(
    actor: AdminKey, affected_permissions: list[str]
) -> None:
    if "root" in affected_permissions:
        require_admin_permission(actor, "root")
    else:
        require_admin_permission(actor, "manage_admins")


def _replace_permissions(db: Session, admin: AdminKey, permissions: list[str]) -> None:
    db.execute(
        delete(AdminPermission).where(AdminPermission.admin_uuid == admin.admin_uuid)
    )
    for permission in permissions:
        db.add(AdminPermission(admin_uuid=admin.admin_uuid, permission=permission))


def _require_admin(db: Session, pubkey: str) -> AdminKey:
    target = db.scalar(select(AdminKey).where(AdminKey.pubkey == pubkey))
    if target is None:
        raise RegistryError(
            ErrorCode.ADMIN_NOT_FOUND,
            "admin key not found",
            {"admin_pubkey": pubkey},
            status_code=404,
        )
    return target


def _admin_conflict(pubkey: str) -> RegistryError:
    return RegistryError(
        ErrorCode.ADMIN_CONFLICT,
        "admin key already exists",
        {"admin_pubkey": pubkey},
        status_code=409,
    )


def _ensure_another_active_root(db: Session, target: AdminKey) -> None:
    active_roots = (
        db.scalars(
            select(AdminKey)
            .join(AdminPermission)
            .where(
                AdminKey.status == "active",
                AdminPermission.permission == "root",
                AdminKey.admin_uuid != target.admin_uuid,
            )
        )
        .unique()
        .all()
    )
    if not active_roots:
        raise RegistryError(
            ErrorCode.LAST_ROOT_ADMIN,
            "cannot remove or demote the last active root admin",
            status_code=409,
        )


def _existing_admin_action(
    db: Session, actor: AdminKey, nonce: str
) -> AdminAction | None:
    return db.scalar(
        select(AdminAction).where(
            AdminAction.actor_admin_uuid == actor.admin_uuid,
            AdminAction.nonce == nonce,
        )
    )


def _existing_asset_admin_action(
    db: Session, actor_pubkey: str, nonce: str
) -> Action | None:
    return db.scalar(
        select(Action).where(
            Action.actor == Actor.ADMIN,
            Action.verified_pubkey == actor_pubkey,
            Action.nonce == nonce,
        )
    )


def _existing_scoped_admin_action(
    db: Session,
    actor: AdminKey,
    actor_pubkey: str,
    action: AdminLifecycleAction | SignedAdminAssetAction,
) -> AdminAction | Action | None:
    if action.operation in ADMIN_LIFECYCLE_OPERATIONS:
        return _existing_admin_action(db, actor, action.nonce)
    return _existing_asset_admin_action(db, actor_pubkey, action.nonce)


def _validate_admin_pubkey(pubkey: str) -> str:
    normalized = normalize_pubkey(pubkey)
    try:
        wally.ec_public_key_verify(bytes.fromhex(normalized))
    except ValueError as exc:
        raise RegistryError(
            ErrorCode.INVALID_PUBKEY,
            "genesis admin public key must be compressed secp256k1",
        ) from exc
    return normalized
