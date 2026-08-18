from sqlalchemy import select
from sqlalchemy.orm import Session

from registry_api.action_writer import new_action
from registry_api.admin_actions import require_admin_permission, verify_admin_asset_action
from registry_api.constants import Actor, Operation
from registry_api.errors import ErrorCode, RegistryError
from registry_api.models import Action, Asset
from registry_api.schemas import AuditEntry, IssuerActionResponse, MigrateAssetAction
from registry_api.serialized_fragments import refresh_asset_serialized_fragments
from registry_api.validation import normalize_asset_id


def migrate_legacy_asset_to_v2(
    db: Session,
    asset_id: str,
    *,
    payload: bytes | None = None,
    signature: str | None = None,
) -> IssuerActionResponse:
    normalized_asset_id = normalize_asset_id(asset_id)
    if payload is not None or signature is not None:
        if payload is None or signature is None:
            raise RegistryError(ErrorCode.INVALID_SIGNATURE, "migration requires a signed admin action", status_code=401)
        verified = verify_admin_asset_action(db, payload=payload, signature=signature)
        if not isinstance(verified.action, MigrateAssetAction):
            raise RegistryError(ErrorCode.VALIDATION_ERROR, "admin action operation must be migrate_asset")
        if verified.action.asset_id != normalized_asset_id:
            raise RegistryError(
                ErrorCode.ASSET_ID_MISMATCH,
                "URL asset_id does not match signed migration asset_id",
                {"url_asset_id": normalized_asset_id, "action_asset_id": verified.action.asset_id},
            )
        require_admin_permission(verified.actor, "migrate_assets")

    asset = db.scalar(select(Asset).where(Asset.asset_id == normalized_asset_id, Asset.status == "active"))
    if asset is None:
        raise RegistryError(ErrorCode.ASSET_NOT_FOUND, "asset not found", {"asset_id": normalized_asset_id}, status_code=404)
    if asset.ticker is None or asset.ticker == "":
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "legacy assets without a ticker cannot be migrated to v2",
            {"asset_id": normalized_asset_id},
        )

    existing = db.scalar(
        select(Action)
        .where(Action.asset_uuid == asset.asset_uuid, Action.operation == Operation.MIGRATE_CONTRACT_METADATA)
        .order_by(Action.audit_sequence.asc())
        .limit(1)
    )
    if existing is not None:
        return IssuerActionResponse(status="idempotent_retry", audit_entry=_audit_entry(existing), asset=None)

    previous_source = asset.initial_issuer_pubkey_source
    asset.initial_issuer_pubkey_source = "migrated_legacy_record"

    action_payload = {
        "operation": Operation.MIGRATE_CONTRACT_METADATA,
        "asset_id": asset.asset_id,
        "from_contract_version": asset.contract_version,
        "to_contract_version": asset.contract_version,
        "previous_initial_issuer_pubkey_source": previous_source,
        "initial_issuer_pubkey_source": asset.initial_issuer_pubkey_source,
    }
    action = new_action(
        asset,
        actor=Actor.SYSTEM,
        operation=Operation.MIGRATE_CONTRACT_METADATA,
        payload=action_payload,
        participates_in_hash_chain=True,
    )
    db.add(action)
    db.flush()
    refresh_asset_serialized_fragments(db, asset)
    db.commit()
    db.refresh(action)
    return IssuerActionResponse(status="applied", audit_entry=_audit_entry(action), asset=None)


def _audit_entry(action: Action) -> AuditEntry:
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
