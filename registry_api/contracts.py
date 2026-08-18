from typing import Any

from registry_api.models import Asset
from registry_api.schemas import ContractMetadataResponse, LegacyAssetRequest

KNOWN_LEGACY_CONTRACT_FIELDS = {
    "entity",
    "issuer_pubkey",
    "name",
    "precision",
    "ticker",
    "version",
}
KNOWN_RESPONSE_CONTRACT_FIELDS = {
    "entity",
    "initial_issuer_pubkey",
    "issuer_pubkey",
    "name",
    "precision",
    "ticker",
    "version",
}


def contract_extra_fields_from_legacy_request(
    request: LegacyAssetRequest,
) -> dict[str, Any]:
    extra_fields = _filtered_extra_fields(
        request.contract.model_extra or {}, KNOWN_LEGACY_CONTRACT_FIELDS
    )
    if request.contract.collection is not None:
        extra_fields["collection"] = request.contract.collection
    return dict(sorted(extra_fields.items()))


def contract_from_asset(asset: Asset) -> dict[str, Any]:
    contract = {
        "entity": {"domain": asset.domain},
        "name": asset.name,
        "precision": asset.precision,
        "version": asset.contract_version,
    }
    if asset.initial_issuer_pubkey_source == "contract":
        contract["initial_issuer_pubkey"] = asset.initial_issuer_pubkey
    else:
        contract["issuer_pubkey"] = asset.initial_issuer_pubkey
    if asset.ticker:
        contract["ticker"] = asset.ticker
    return _merge_extra_fields(contract, asset.contract_extra_fields or {})


def v2_response_contract_from_asset(asset: Asset) -> ContractMetadataResponse:
    return ContractMetadataResponse.model_validate(contract_from_asset(asset))


def legacy_contract_from_asset(asset: Asset) -> dict[str, Any]:
    return contract_from_asset(asset)


def _merge_extra_fields(
    contract: dict[str, Any], extra_fields: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(contract)
    for key, value in sorted(extra_fields.items()):
        if key not in KNOWN_RESPONSE_CONTRACT_FIELDS:
            merged[key] = value
    return merged


def _filtered_extra_fields(
    extra_fields: dict[str, Any], known_fields: set[str]
) -> dict[str, Any]:
    return {
        key: value
        for key, value in sorted(extra_fields.items())
        if key not in known_fields
    }
