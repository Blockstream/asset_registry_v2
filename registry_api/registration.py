from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from registry_api.asset_registration import new_registered_asset, new_registration_action
from registry_api.canonical_json import contract_hash
from registry_api.chain import ChainVerifier, IssuanceCommitment, TrustingChainVerifier, UnconfiguredChainVerifier
from registry_api.constants import Operation
from registry_api.domain_verification import DomainProof, HttpTextFetcher, TxtResolver, verify_domain_proof
from registry_api.errors import ErrorCode, RegistryError
from registry_api.legacy_response import legacy_registration_response
from registry_api.models import AssetAdminAnnotation, AssetMutableMetadata, IssuerPubkeyHistory
from registry_api.registration_command import command_from_legacy_registration
from registry_api.schemas import LegacyAssetRequest
from registry_api.serialized_fragments import refresh_asset_serialized_fragments


def _chain_verifier(enforce_chain_verification: bool, chain_verifier: ChainVerifier | None = None) -> ChainVerifier:
    if chain_verifier is not None:
        return chain_verifier if enforce_chain_verification else TrustingChainVerifier()
    return UnconfiguredChainVerifier() if enforce_chain_verification else TrustingChainVerifier()


def register_legacy_asset(
    db: Session,
    request: LegacyAssetRequest,
    *,
    enforce_chain_verification: bool = False,
    enforce_domain_verification: bool = False,
    chain_verifier: ChainVerifier | None = None,
    fetch_text: HttpTextFetcher | None = None,
    resolve_txt: TxtResolver | None = None,
    make_response: Callable[[LegacyAssetRequest], dict[str, Any]] = legacy_registration_response,
) -> dict[str, Any]:
    command = command_from_legacy_registration(request)
    method = command.domain_verification_method
    hash_hex = contract_hash(request.contract.model_dump(exclude_none=True))

    _chain_verifier(enforce_chain_verification, chain_verifier).verify_issuance_commitment(
        IssuanceCommitment(asset_id=request.asset_id, contract_hash=hash_hex)
    )

    if enforce_domain_verification:
        verify_domain_proof(
            DomainProof(request.contract.entity.domain, request.asset_id, request.contract.ticker),
            method,
            fetch_text=fetch_text,
            resolve_txt=resolve_txt,
        )

    response = make_response(request)

    asset = new_registered_asset(command)

    try:
        db.add(asset)
        db.flush()

        action = new_registration_action(
            asset,
            operation=Operation.LEGACY_REGISTER,
            payload={"request": response, "contract_hash": hash_hex},
            participates_in_hash_chain=False,
        )
        db.add(action)
        db.flush()

        db.add(AssetMutableMetadata(asset_uuid=asset.asset_uuid, schema_version=1))
        db.add(AssetAdminAnnotation(asset_uuid=asset.asset_uuid))
        db.add(
            IssuerPubkeyHistory(
                asset_uuid=asset.asset_uuid,
                pubkey=asset.current_issuer_pubkey,
                valid_from_action_uuid=action.action_uuid,
            )
        )
        db.flush()
        refresh_asset_serialized_fragments(db, asset)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RegistryError(
            ErrorCode.ASSET_CONFLICT,
            "asset is already registered or conflicts with an active namespace",
            {"asset_id": request.asset_id},
            status_code=409,
        ) from exc
    except Exception:
        db.rollback()
        raise

    return response
