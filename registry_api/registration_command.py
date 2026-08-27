from dataclasses import dataclass
from typing import Any, Literal

from registry_api.contracts import contract_extra_fields_from_legacy_request
from registry_api.schemas import LegacyAssetRequest, MutableMetadata, RegisterAssetRequest


@dataclass(frozen=True)
class RegisterAssetCommand:
    asset_id: str
    contract: dict[str, Any]
    contract_version: int
    domain: str
    name: str
    ticker: str | None
    precision: int
    domain_verification_method: str
    initial_issuer_pubkey: str
    initial_issuer_pubkey_source: str
    contract_extra_fields: dict[str, Any]
    mutable: MutableMetadata
    source: Literal["legacy", "v2"]


def command_from_legacy_registration(request: LegacyAssetRequest) -> RegisterAssetCommand:
    method = request.domain_verification_method or "http"
    return RegisterAssetCommand(
        asset_id=request.asset_id,
        contract=request.contract.model_dump(exclude_unset=True),
        contract_version=request.contract.version,
        domain=request.contract.entity.domain,
        name=request.contract.name,
        ticker=request.contract.ticker,
        precision=request.contract.precision,
        domain_verification_method=method,
        initial_issuer_pubkey=request.contract.issuer_pubkey,
        initial_issuer_pubkey_source="registry_registration",
        contract_extra_fields=contract_extra_fields_from_legacy_request(request),
        mutable=MutableMetadata(),
        source="legacy",
    )


def command_from_v2_registration(request: RegisterAssetRequest, initial_pubkey: str) -> RegisterAssetCommand:
    method = request.domain_verification_method or "http"
    return RegisterAssetCommand(
        asset_id=request.asset_id,
        contract=request.contract.model_dump(exclude_none=True),
        contract_version=request.contract.version,
        domain=request.contract.entity.domain,
        name=request.contract.name,
        ticker=request.contract.ticker,
        precision=request.contract.precision,
        domain_verification_method=method,
        initial_issuer_pubkey=initial_pubkey,
        initial_issuer_pubkey_source="contract" if request.contract.initial_issuer_pubkey is not None else "registry_registration",
        contract_extra_fields={},
        mutable=request.mutable,
        source="v2",
    )
