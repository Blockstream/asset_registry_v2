from typing import Any

from registry_api.canonical_json import action_hash as compute_action_hash
from registry_api.models import Action, Asset


def new_action(
    asset: Asset,
    *,
    actor: str,
    operation: str,
    payload: dict[str, Any],
    signature: str | None = None,
    nonce: str | None = None,
    issuer_timestamp: Any | None = None,
    verified_pubkey: str | None = None,
    admin_id: str | None = None,
    participates_in_hash_chain: bool = False,
) -> Action:
    return Action(
        asset_uuid=asset.asset_uuid,
        asset_chain_id=asset.asset_id,
        actor=actor,
        operation=operation,
        action=payload,
        signature=signature,
        nonce=nonce,
        issuer_timestamp=issuer_timestamp,
        verified_pubkey=verified_pubkey,
        admin_id=admin_id,
        action_hash=compute_action_hash(payload) if participates_in_hash_chain else None,
    )
