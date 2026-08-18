from typing import Any

from registry_api.models import Asset
from registry_api.schemas import LegacyAssetRequest
from registry_api.contracts import legacy_contract_from_asset


TESTNET_LEGACY_UNCOMPRESSED_ISSUER_PUBKEYS = {
    asset_id: (
        "04d8bb0680c89a19042a4c30f52ebb334dc075bcd53191e4de3885faa0eb6437db"
        "56a4e71286ab497274361ef558d4c69eaacdb3eacbe7a469f25de95636db4117"
    )
    for asset_id in (
        "92ac106ea13e65fe1ccae10ca69d6bd14147bb445f4c6119b8db5d027c543d15",
        "95b3d6bba96c0610e058fe5aa31ac7d3861279528d1550ee6ce9237118078a61",
        "e4c76430b56dbd0522f4cf77cb0fe4e652f7b7ac1a2cacac0d336021405a3404",
        "88867e59f6aef276706b4253085cadd38ef9836e2e8c0b94bb482f73d07e551e",
    )
}


def legacy_registration_response(request: LegacyAssetRequest) -> dict[str, Any]:
    contract = request.contract.model_dump(exclude_none=True)
    response = {
        "asset_id": request.asset_id,
        "contract": contract,
        "version": request.contract.version,
        "issuer_pubkey": request.contract.issuer_pubkey,
        "name": request.contract.name,
        "precision": request.contract.precision,
        "entity": {"domain": request.contract.entity.domain},
    }
    if request.contract.ticker is not None:
        response["ticker"] = request.contract.ticker
    if request.contract.collection is not None:
        response["collection"] = request.contract.collection
    return response


def legacy_response_from_asset(
    asset: Asset, registered_response: dict[str, Any] | None = None
) -> dict[str, Any]:
    if registered_response is not None:
        response = dict(registered_response)
        response["asset_id"] = asset.asset_id
        _restore_legacy_uncompressed_issuer_pubkey(response, asset.asset_id)
        return response

    contract = legacy_contract_from_asset(asset)

    response: dict[str, Any] = {
        "asset_id": asset.asset_id,
        "contract": contract,
        "version": asset.contract_version,
        "name": asset.name,
        "precision": asset.precision,
        "entity": {"domain": asset.domain},
    }
    if asset.initial_issuer_pubkey_source != "contract":
        response["issuer_pubkey"] = asset.initial_issuer_pubkey
    if asset.ticker:
        response["ticker"] = asset.ticker
    _restore_legacy_uncompressed_issuer_pubkey(response, asset.asset_id)
    return response


def _restore_legacy_uncompressed_issuer_pubkey(
    response: dict[str, Any], asset_id: str
) -> None:
    issuer_pubkey = TESTNET_LEGACY_UNCOMPRESSED_ISSUER_PUBKEYS.get(asset_id)
    if issuer_pubkey is None:
        return

    response["issuer_pubkey"] = issuer_pubkey
    if isinstance(response.get("contract"), dict):
        response["contract"] = dict(response["contract"])
        response["contract"]["issuer_pubkey"] = issuer_pubkey
