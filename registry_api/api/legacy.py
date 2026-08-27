from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session
from starlette.convertors import CONVERTOR_TYPES, Convertor
from starlette.responses import StreamingResponse

from registry_api.api.query_validation import reject_unknown_query_parameters
from registry_api.api.responses import (
    RATE_LIMIT_ERROR_RESPONSES,
    STANDARD_ERROR_RESPONSES,
)
from registry_api.canonical_json import contract_hash
from registry_api.chain import EsploraChainVerifier
from registry_api.db import get_db
from registry_api.errors import ErrorCode, RegistryError
from registry_api.http_clients import HttpxProofClient
from registry_api.icons import (
    liquid_mainnet_policy_asset_icon_fallback,
    stream_icon_map_bytes,
)
from registry_api.legacy_assets import deregister_legacy_asset, get_legacy_asset
from registry_api.legacy_response import legacy_registration_response
from registry_api.rate_limit import registration_rate_limit
from registry_api.registration import register_legacy_asset
from registry_api.schemas import (
    AssetId,
    LegacyAssetRequest,
    LegacyContractValidationRequest,
    LegacyDeletionRequest,
)
from registry_api.serialized_fragments import stream_legacy_all_json_bytes
from registry_api.settings import Settings, get_settings
from registry_api.shadow import (
    ensure_legacy_deregistration_written,
    ensure_legacy_registration_written,
)


class AssetIdConvertor(Convertor[str]):
    regex = "[0-9a-fA-F]{64}"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


CONVERTOR_TYPES.setdefault("asset_id", AssetIdConvertor())

router = APIRouter(
    tags=["Legacy"],
    responses=STANDARD_ERROR_RESPONSES,
    dependencies=[Depends(reject_unknown_query_parameters)],
)


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    operation_id="registerAssetLegacyRoot",
    summary="Legacy registration endpoint",
    responses=RATE_LIMIT_ERROR_RESPONSES,
)
def register_asset_legacy_root(
    request: LegacyAssetRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    _rate_limit: Annotated[None, Depends(registration_rate_limit)],
) -> dict[str, Any]:
    """Register an asset through the legacy-compatible flow. New clients should use `POST /v2/assets`."""
    proof_client = HttpxProofClient(
        timeout=settings.http_timeout_seconds,
        dns_over_https_url=settings.dns_over_https_url,
        domain_fetch_failure_cooldown_seconds=settings.domain_fetch_failure_cooldown_seconds,
        domain_fetch_quota=settings.domain_fetch_quota,
        domain_fetch_quota_window_seconds=settings.domain_fetch_quota_window_seconds,
        max_concurrent_fetches=settings.max_concurrent_proof_fetches,
    )
    chain_verifier = EsploraChainVerifier(
        settings.esplora_url, timeout=settings.http_timeout_seconds
    )
    local_error: RegistryError | None = None
    try:
        registration_response = register_legacy_asset(
            db,
            request,
            enforce_chain_verification=settings.enforce_chain_verification,
            enforce_domain_verification=settings.enforce_domain_verification,
            chain_verifier=chain_verifier,
            fetch_text=proof_client.fetch_text,
            resolve_txt=proof_client.resolve_txt_google,
        )
    except RegistryError as exc:
        if not settings.legacy_shadow_write or exc.error != ErrorCode.ASSET_CONFLICT:
            raise
        local_error = exc
        registration_response = legacy_registration_response(request)

    shadow_outcome = ensure_legacy_registration_written(settings, request)
    if shadow_outcome.legacy_status_code is not None:
        response.status_code = shadow_outcome.legacy_status_code
    elif local_error is not None:
        raise local_error

    return registration_response


@router.get(
    "/",
    operation_id="getAllAssetsLegacyRoot",
    summary="Legacy all-assets endpoint",
    responses={
        200: {
            "description": "Legacy asset listing keyed by asset ID.",
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    },
)
@router.get(
    "/index.json",
    operation_id="getAllAssetsLegacyIndex",
    summary="Legacy all-assets JSON endpoint",
    responses={
        200: {
            "description": "Legacy asset listing keyed by asset ID.",
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    },
)
def list_assets_legacy_root() -> StreamingResponse:
    """Return the legacy-compatible asset object keyed by asset ID."""
    return StreamingResponse(
        stream_legacy_all_json_bytes(), media_type="application/json"
    )


@router.get(
    "/icons.json",
    response_model=None,
    operation_id="getIconsLegacy",
    summary="Get approved asset icons",
    responses={
        200: {
            "description": "Base64 PNG icons keyed by asset ID.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    }
                }
            },
        }
    },
)
def get_icons_legacy(
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Return current approved icons as raw Base64 values keyed by asset ID."""
    # Compatibility fallback until the Liquid policy asset has a registry-backed icon.
    # stream_icon_map_bytes gives a future approved database icon precedence.
    fallback_icons = (
        liquid_mainnet_policy_asset_icon_fallback()
        if settings.network == "liquid"
        else None
    )
    return StreamingResponse(
        stream_icon_map_bytes(fallback_icons=fallback_icons),
        media_type="application/json",
    )


@router.post(
    "/contract/validate",
    operation_id="validateContractLegacy",
    summary="Legacy contract validation endpoint",
)
def validate_contract_legacy(request: LegacyContractValidationRequest) -> Response:
    """Validate legacy version 0 contract metadata against its supplied contract hash."""
    contract = request.contract.model_dump(mode="json", exclude_unset=True)
    expected_hash = contract_hash(contract)
    if expected_hash != request.contract_hash:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            f"contract hash mismatch, expected {expected_hash}",
            {"expected_contract_hash": expected_hash},
            status_code=409,
        )

    return Response(content="valid", media_type="text/plain")


@router.get(
    "/{asset_id:asset_id}",
    operation_id="getAssetLegacyRoot",
    summary="Legacy asset lookup endpoint",
)
def get_asset_legacy_root(
    asset_id: AssetId, db: Annotated[Session, Depends(get_db)]
) -> dict[str, Any]:
    """Return one active asset in the legacy-compatible response shape."""
    return get_legacy_asset(db, asset_id)


@router.delete(
    "/{asset_id:asset_id}",
    operation_id="deleteAssetLegacyRoot",
    summary="Legacy asset deregistration endpoint",
)
def delete_asset_legacy_root(
    asset_id: AssetId,
    request: LegacyDeletionRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Deregister an active legacy asset after verifying its legacy deletion signature."""
    local_error: RegistryError | None = None
    try:
        message = deregister_legacy_asset(db, asset_id, request.signature)
    except RegistryError as exc:
        if not settings.legacy_shadow_write:
            raise
        local_error = exc
        message = None

    shadow_outcome = ensure_legacy_deregistration_written(settings, asset_id, request)
    status_code = shadow_outcome.legacy_status_code
    if status_code is None:
        if local_error is not None:
            raise local_error
        status_code = status.HTTP_200_OK

    if status_code >= 400 and local_error is not None:
        raise local_error

    if message is None and isinstance(shadow_outcome.legacy_response, str):
        message = shadow_outcome.legacy_response
    return Response(
        content=message or "", media_type="text/plain", status_code=status_code
    )
