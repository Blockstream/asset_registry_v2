from registry_api.action_writer import new_action
from registry_api.constants import Actor
from registry_api.models import Action, Asset
from registry_api.registration_command import RegisterAssetCommand


def new_registered_asset(command: RegisterAssetCommand) -> Asset:
    return Asset(
        asset_id=command.asset_id,
        contract_version=command.contract_version,
        domain=command.domain,
        name=command.name,
        ticker=command.ticker,
        precision=command.precision,
        contract_extra_fields=command.contract_extra_fields,
        domain_verification_method=command.domain_verification_method,
        initial_issuer_pubkey=command.initial_issuer_pubkey,
        initial_issuer_pubkey_source=command.initial_issuer_pubkey_source,
        current_issuer_pubkey=command.initial_issuer_pubkey,
        mutable_schema_version=1,
        status="active",
    )


def new_registration_action(
    asset: Asset,
    *,
    operation: str,
    payload: dict,
    participates_in_hash_chain: bool,
) -> Action:
    return new_action(
        asset,
        actor=Actor.SYSTEM,
        operation=operation,
        payload=payload,
        participates_in_hash_chain=participates_in_hash_chain,
    )
