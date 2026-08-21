from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Header, Query, Request, status
from pydantic import BeforeValidator
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse, Response, StreamingResponse

from registry_api.admin import submit_admin_asset_action, update_admin_annotations
from registry_api.admin_actions import (
    bootstrap_genesis_admin,
    submit_admin_lifecycle_action,
)
from registry_api.api.query_validation import reject_unknown_query_parameters
from registry_api.api.responses import (
    RATE_LIMIT_ERROR_RESPONSES,
    STANDARD_ERROR_RESPONSES,
)
from registry_api.audit import MAX_AUDIT_ID, get_asset_audit_log, search_audit_log
from registry_api.canonical_json import canonical_json_bytes
from registry_api.chain import EsploraChainVerifier
from registry_api.constants import Actor
from registry_api.db import get_db
from registry_api.http_clients import HttpxProofClient
from registry_api.icons import (
    decide_icon_proposal,
    published_icon_for_asset,
    published_icon_for_asset_by_hash,
    require_published_icon_for_asset_by_hash,
    search_issuer_icon_proposals,
    search_pending_icon_proposals,
    set_admin_asset_icon,
    submit_icon_proposal,
)
from registry_api.issuer_actions import get_latest_action_hash, submit_issuer_action
from registry_api.migration import migrate_legacy_asset_to_v2
from registry_api.openapi_metadata import (
    ADMIN_ANNOTATION_EXAMPLES,
    ADMIN_ASSET_EXAMPLES,
    ADMIN_ICON_SEARCH_EXAMPLES,
    ADMIN_ICON_UPLOAD_EXAMPLES,
    ADMIN_LIFECYCLE_EXAMPLES,
    ISSUER_ACTION_EXAMPLES,
    ICON_PROPOSAL_EXAMPLES,
    ISSUER_ICON_SEARCH_EXAMPLES,
    REGISTER_ASSET_EXAMPLES,
)
from registry_api.rate_limit import registration_rate_limit
from registry_api.schemas import (
    AdminActionResponse,
    AdminAssetAction,
    AdminIconUploadRequest,
    AdminLifecycleAction,
    AssetId,
    AssetListResponse,
    AssetResponse,
    AuditLogResponse,
    IssuerActionResponse,
    IssuerAction,
    IconProposalRequest,
    IconProposalResponse,
    IconHash,
    IssuerIconProposalListResponse,
    IssuerIconProposalSearchRequest,
    LatestActionHashResponse,
    MigrateAssetAction,
    PendingIconProposalListResponse,
    PendingIconProposalSearchRequest,
    ApproveIconAction,
    RejectIconAction,
    RegisterAssetRequest,
    UpdateAdminAnnotationsAction,
)
from registry_api.serialized_fragments import stream_v2_all_json_bytes
from registry_api.settings import Settings, get_settings
from registry_api.v2_assets import (
    asset_icon_href,
    get_v2_asset,
    register_v2_asset,
    search_v2_assets,
)
from registry_api.validation import CASE_INSENSITIVE_DOMAIN_PATTERN

router = APIRouter(
    prefix="/v2",
    responses=STANDARD_ERROR_RESPONSES,
    dependencies=[Depends(reject_unknown_query_parameters)],
)


def _require_rfc3339_datetime(value: object) -> object:
    if isinstance(value, str) and "T" not in value and "t" not in value:
        raise ValueError("timestamp must be an RFC 3339 date-time")
    return value


def _lowercase_query_value(value: object) -> object:
    return value.lower() if isinstance(value, str) else value


def _canonicalize_asset_type_query(value: object) -> object:
    if not isinstance(value, str):
        return value
    return {
        "amp_asset": "AMP_asset",
        "stablecoin": "stablecoin",
        "security_token": "security_token",
        "other": "other",
    }.get(value.casefold(), value)


Rfc3339DateTime = Annotated[datetime, BeforeValidator(_require_rfc3339_datetime)]
CategoryTagFilter = Annotated[
    Literal["stablecoin", "bond", "fixed-income", "tokenized"],
    BeforeValidator(_lowercase_query_value),
]
IMMUTABLE_ICON_CACHE_CONTROL = "public, max-age=31536000, immutable"


def _icon_etag(icon_hash: str) -> str:
    return f'"{icon_hash}"'


def _normalize_etag_candidate(value: str) -> str:
    return value.strip().removeprefix("W/").strip()


def _request_condition_matches_etag(value: str | None, etag: str) -> bool:
    if value is None:
        return False
    return any(
        _normalize_etag_candidate(candidate) in {"*", etag}
        for candidate in value.split(",")
    )


def ensure_genesis_admin(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    bootstrap_genesis_admin(db, settings.genesis_admin_pubkey)


@router.post(
    "/assets",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Assets"],
    operation_id="registerAssetV2",
    summary="Register a Liquid asset",
    responses=RATE_LIMIT_ERROR_RESPONSES,
)
def register_asset_v2(
    request: Annotated[
        RegisterAssetRequest, Body(openapi_examples=REGISTER_ASSET_EXAMPLES)
    ],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    _rate_limit: Annotated[None, Depends(registration_rate_limit)],
    asset_registry_signature: Annotated[
        str | None, Header(alias="Asset-Registry-Signature")
    ] = None,
) -> AssetResponse:
    """Register immutable contract metadata after chain and issuer-domain verification."""
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
    return register_v2_asset(
        db,
        request,
        enforce_chain_verification=settings.enforce_chain_verification,
        enforce_domain_verification=settings.enforce_domain_verification,
        chain_verifier=chain_verifier,
        fetch_text=proof_client.fetch_text,
        resolve_txt=proof_client.resolve_txt_google,
        domain_signature=asset_registry_signature,
        registration_payload=canonical_json_bytes(
            request.model_dump(mode="json", exclude_none=True)
        ),
    )


@router.get(
    "/assets",
    response_model=AssetListResponse,
    tags=["Assets"],
    operation_id="searchAssetsV2",
    summary="Search and list assets",
)
def search_assets_v2(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    sort: Literal[
        "asset_id_asc",
        "domain_asc",
        "domain_desc",
        "name_asc",
        "name_desc",
        "ticker_asc",
        "ticker_desc",
        "created_at_desc",
        "updated_at_desc",
    ] = "asset_id_asc",
    asset_id: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=64,
            pattern=r"^[0-9A-Fa-f]+$",
            description="Case-insensitive asset ID prefix.",
        ),
    ] = None,
    domain: Annotated[
        str | None,
        Query(
            min_length=3,
            max_length=255,
            pattern=CASE_INSENSITIVE_DOMAIN_PATTERN,
            description="Case-insensitive exact domain match.",
        ),
    ] = None,
    ticker: Annotated[
        str | None,
        Query(
            max_length=24,
            pattern=r"^[^\x00]*$",
            description="Case-insensitive ticker prefix.",
        ),
    ] = None,
    name: Annotated[
        str | None,
        Query(
            max_length=255,
            pattern=r"^[^\x00]*$",
            description="Case-insensitive asset name prefix.",
        ),
    ] = None,
    asset_type: Annotated[
        Literal["AMP_asset", "stablecoin", "security_token", "other"] | None,
        BeforeValidator(_canonicalize_asset_type_query),
        Query(description="Case-insensitive exact asset type match."),
    ] = None,
    category_tag: Annotated[
        list[CategoryTagFilter] | None,
        Query(
            description=(
                "Case-insensitive category tag filter. Multiple values match assets "
                "with any of the supplied tags."
            )
        ),
    ] = None,
    trading_venue: Annotated[
        Literal["sideswap", "bitfinex"] | None,
        BeforeValidator(_lowercase_query_value),
        Query(description="Case-insensitive exact trading venue match."),
    ] = None,
    created_after: Annotated[
        Rfc3339DateTime | None,
        Query(description="Return assets created strictly after this timestamp."),
    ] = None,
    updated_after: Annotated[
        Rfc3339DateTime | None,
        Query(description="Return assets updated strictly after this timestamp."),
    ] = None,
) -> AssetListResponse:
    """Return a paginated, filtered asset list with stable deterministic sorting."""
    return search_v2_assets(
        db,
        page=page,
        page_size=page_size,
        sort=sort,
        asset_id=asset_id,
        domain=domain,
        ticker=ticker,
        name=name,
        asset_type=asset_type,
        category_tag=category_tag,
        trading_venue=trading_venue,
        created_after=created_after,
        updated_after=updated_after,
    )


@router.get(
    "/assets/all.json",
    response_model=None,
    tags=["Assets"],
    operation_id="getAllAssetsV2Json",
    summary="Get all v2-normalized assets as a single JSON object",
    responses={
        200: {
            "model": dict[str, AssetResponse],
            "description": "Object keyed by asset ID.",
        }
    },
)
def all_assets_v2_json() -> StreamingResponse:
    """Compatibility endpoint for consumers that require one object keyed by asset ID."""
    return StreamingResponse(stream_v2_all_json_bytes(), media_type="application/json")


@router.get(
    "/assets/{asset_id}/icon",
    response_model=None,
    response_class=RedirectResponse,
    tags=["Assets"],
    operation_id="getCurrentAssetIconV2",
    summary="Redirect to the current approved asset icon",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    responses={
        307: {
            "description": "Redirect to the content-addressed current icon.",
            "headers": {
                "Location": {
                    "description": "Registry-relative content-addressed icon URL.",
                    "schema": {"type": "string"},
                },
                "Cache-Control": {
                    "description": "Requires revalidation of the current-icon redirect.",
                    "schema": {"type": "string"},
                },
            },
        }
    },
)
def get_current_asset_icon_v2(
    asset_id: AssetId,
    db: Annotated[Session, Depends(get_db)],
) -> RedirectResponse:
    """Redirect to the immutable URL for an asset's current approved icon."""
    proposal = published_icon_for_asset(db, asset_id, include_image_data=False)
    location = asset_icon_href(asset_id.lower(), proposal.icon_hash)
    return RedirectResponse(
        location,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Cache-Control": "no-cache"},
    )


@router.get(
    "/assets/{asset_id}/icon/{icon_hash}.png",
    response_model=None,
    response_class=Response,
    tags=["Assets"],
    operation_id="getAssetIconByHashV2",
    summary="Get a content-addressed approved asset icon",
    responses={
        200: {
            "description": "Previously published PNG icon matching the content hash.",
            "content": {
                "image/png": {
                    "schema": {"type": "string", "format": "binary"},
                }
            },
            "headers": {
                "Cache-Control": {
                    "schema": {"type": "string"},
                },
                "ETag": {
                    "schema": {"type": "string"},
                },
                "X-Content-Type-Options": {
                    "description": "Disables MIME-type sniffing for stored icon bytes.",
                    "schema": {"type": "string", "enum": ["nosniff"]},
                },
            },
        },
        304: {
            "description": "The cached icon matches If-None-Match.",
            "headers": {
                "Cache-Control": {
                    "schema": {"type": "string"},
                },
                "ETag": {
                    "schema": {"type": "string"},
                },
                "X-Content-Type-Options": {
                    "description": "Disables MIME-type sniffing for stored icon bytes.",
                    "schema": {"type": "string", "enum": ["nosniff"]},
                },
            },
        },
    },
)
def get_asset_icon_by_hash_v2(
    asset_id: AssetId,
    icon_hash: IconHash,
    db: Annotated[Session, Depends(get_db)],
    if_none_match: Annotated[
        str | None,
        Header(
            alias="If-None-Match",
            description="ETag from a previously cached version of this icon.",
        ),
    ] = None,
) -> Response:
    """Return previously published PNG bytes at a content-addressed URL."""
    etag = _icon_etag(icon_hash.lower())
    condition_matches = _request_condition_matches_etag(if_none_match, etag)
    headers = {
        "Cache-Control": IMMUTABLE_ICON_CACHE_CONTROL,
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }
    if condition_matches:
        require_published_icon_for_asset_by_hash(db, asset_id, icon_hash)
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    proposal = published_icon_for_asset_by_hash(db, asset_id, icon_hash)
    assert proposal.image_data is not None
    return Response(
        content=proposal.image_data,
        media_type="image/png",
        headers=headers,
    )


@router.get(
    "/assets/{asset_id}",
    response_model=AssetResponse,
    tags=["Assets"],
    operation_id="getAssetV2",
    summary="Get an asset by asset ID",
)
def get_asset_v2(
    asset_id: AssetId,
    db: Annotated[Session, Depends(get_db)],
) -> AssetResponse:
    """Return the active v2-normalized asset record for an asset ID."""
    return get_v2_asset(db, asset_id)


@router.get(
    "/assets/{asset_id}/audit",
    response_model=AuditLogResponse,
    tags=["Audit"],
    operation_id="getAssetAuditLogV2",
    summary="Get audit log for an asset",
)
def get_asset_audit_v2(
    asset_id: AssetId,
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    since_audit_id: Annotated[int, Query(ge=0, le=MAX_AUDIT_ID)] = 0,
    order: Literal["asc", "desc"] = "asc",
) -> AuditLogResponse:
    """Return append-only audit entries for one asset ordered by audit ID."""
    return get_asset_audit_log(
        db,
        asset_id=asset_id,
        since_audit_id=since_audit_id,
        limit=limit,
        order=order,
    )


@router.get(
    "/audit",
    response_model=AuditLogResponse,
    tags=["Audit"],
    operation_id="searchAuditLogV2",
    summary="Search global audit log",
)
def search_audit_v2(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    since_audit_id: Annotated[int, Query(ge=0, le=MAX_AUDIT_ID)] = 0,
    asset_id: AssetId | None = None,
    operation: Annotated[
        str | None, Query(max_length=128, pattern=r"^[^\x00]*$")
    ] = None,
    actor: Literal[Actor.ISSUER, Actor.ADMIN, Actor.SYSTEM] | None = None,  # pyright: ignore[reportInvalidTypeForm]
    from_server_received_at: Rfc3339DateTime | None = None,
    to_server_received_at: Rfc3339DateTime | None = None,
    order: Literal["asc", "desc"] = "asc",
) -> AuditLogResponse:
    """Search the merged asset-action and admin-lifecycle audit stream."""
    return search_audit_log(
        db,
        since_audit_id=since_audit_id,
        limit=limit,
        asset_id=asset_id,
        operation=operation,
        actor=actor,
        from_server_received_at=from_server_received_at,
        to_server_received_at=to_server_received_at,
        order=order,
    )


@router.post(
    "/assets/{asset_id}/actions",
    response_model=IssuerActionResponse,
    tags=["Issuer Actions"],
    operation_id="submitIssuerActionV2",
    summary="Submit a signed issuer action",
)
async def submit_asset_issuer_action_v2(
    asset_id: AssetId,
    _action: Annotated[IssuerAction, Body(openapi_examples=ISSUER_ACTION_EXAMPLES)],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    asset_registry_signature: Annotated[
        str, Header(alias="Asset-Registry-Signature", min_length=1)
    ],
) -> IssuerActionResponse:
    """Apply a canonical signed issuer mutation linked to the latest issuer action hash."""
    return submit_issuer_action(
        db,
        asset_id=asset_id,
        payload=await request.body(),
        signature=asset_registry_signature,
    )


@router.get(
    "/assets/{asset_id}/actions/latest",
    response_model=LatestActionHashResponse,
    tags=["Issuer Actions"],
    operation_id="getLatestIssuerActionHashV2",
    summary="Get latest issuer action hash",
)
def get_latest_asset_action_hash_v2(
    asset_id: AssetId,
    db: Annotated[Session, Depends(get_db)],
) -> LatestActionHashResponse:
    """Return the hash required as `prev_action_hash` by the next issuer action."""
    return get_latest_action_hash(db, asset_id)


@router.post(
    "/assets/{asset_id}/icon-proposals",
    response_model=IconProposalResponse,
    tags=["Issuer Actions"],
    operation_id="submitIconProposalV2",
    summary="Submit a signed asset icon proposal",
)
def submit_asset_icon_proposal_v2(
    asset_id: AssetId,
    request: Annotated[
        IconProposalRequest, Body(openapi_examples=ICON_PROPOSAL_EXAMPLES)
    ],
    db: Annotated[Session, Depends(get_db)],
    asset_registry_signature: Annotated[
        str, Header(alias="Asset-Registry-Signature", min_length=1)
    ],
) -> IconProposalResponse:
    """Verify a signature over the nested action and store its separately transported PNG bytes."""
    return submit_icon_proposal(
        db,
        asset_id=asset_id,
        action=request.action,
        icon=request.icon,
        signature=asset_registry_signature,
    )


@router.post(
    "/assets/{asset_id}/icon-proposals/search",
    response_model=IssuerIconProposalListResponse,
    tags=["Issuer Actions"],
    operation_id="searchIssuerIconProposalsV2",
    summary="Search icon proposals made by an issuer key",
)
async def search_issuer_icon_proposals_v2(
    asset_id: AssetId,
    _query: Annotated[
        IssuerIconProposalSearchRequest,
        Body(openapi_examples=ISSUER_ICON_SEARCH_EXAMPLES),
    ],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    asset_registry_signature: Annotated[
        str, Header(alias="Asset-Registry-Signature", min_length=1)
    ],
) -> IssuerIconProposalListResponse:
    """Return only proposals whose submission was signed by the requesting key."""
    return search_issuer_icon_proposals(
        db,
        asset_id=asset_id,
        payload=await request.body(),
        signature=asset_registry_signature,
    )


@router.post(
    "/assets/{asset_id}/migrate",
    response_model=IssuerActionResponse,
    tags=["Assets"],
    operation_id="migrateLegacyAssetToV2",
    summary="Mark a legacy asset as v2-managed",
    responses=RATE_LIMIT_ERROR_RESPONSES,
)
async def migrate_legacy_asset(
    asset_id: AssetId,
    _action: MigrateAssetAction,
    request: Request,
    _rate_limit: Annotated[None, Depends(registration_rate_limit)],
    db: Annotated[Session, Depends(get_db)],
    _bootstrap: Annotated[None, Depends(ensure_genesis_admin)],
    asset_registry_admin_signature: Annotated[
        str, Header(alias="Asset-Registry-Admin-Signature", min_length=1)
    ],
) -> IssuerActionResponse:
    """Apply a signed admin migration without altering the asset's chain contract."""
    return migrate_legacy_asset_to_v2(
        db,
        asset_id,
        payload=await request.body(),
        signature=asset_registry_admin_signature,
    )


@router.post(
    "/admin/actions",
    response_model=AdminActionResponse,
    tags=["Admin"],
    operation_id="submitAdminActionV2",
    summary="Submit a signed admin lifecycle action",
)
async def submit_admin_action_v2(
    _action: Annotated[
        AdminLifecycleAction, Body(openapi_examples=ADMIN_LIFECYCLE_EXAMPLES)
    ],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    _bootstrap: Annotated[None, Depends(ensure_genesis_admin)],
    asset_registry_admin_signature: Annotated[
        str, Header(alias="Asset-Registry-Admin-Signature", min_length=1)
    ],
) -> AdminActionResponse:
    """Manage admin keys and permissions through a canonical signed action."""
    return submit_admin_lifecycle_action(
        db,
        payload=await request.body(),
        signature=asset_registry_admin_signature,
    )


@router.post(
    "/admin/icon-proposals/search",
    response_model=PendingIconProposalListResponse,
    tags=["Admin"],
    operation_id="searchPendingIconProposalsV2",
    summary="Search pending icon proposals",
)
async def search_pending_icon_proposals_v2(
    _query: Annotated[
        PendingIconProposalSearchRequest,
        Body(openapi_examples=ADMIN_ICON_SEARCH_EXAMPLES),
    ],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    _bootstrap: Annotated[None, Depends(ensure_genesis_admin)],
    asset_registry_admin_signature: Annotated[
        str, Header(alias="Asset-Registry-Admin-Signature", min_length=1)
    ],
) -> PendingIconProposalListResponse:
    """Return pending raw icons to an active admin with the review_icons permission."""
    return search_pending_icon_proposals(
        db,
        payload=await request.body(),
        signature=asset_registry_admin_signature,
    )


@router.put(
    "/admin/assets/{asset_id}/icon",
    response_model=AssetResponse,
    tags=["Admin"],
    operation_id="setAdminAssetIconV2",
    summary="Upload and assign an asset icon",
)
def set_admin_asset_icon_v2(
    asset_id: AssetId,
    request: Annotated[
        AdminIconUploadRequest,
        Body(openapi_examples=ADMIN_ICON_UPLOAD_EXAMPLES),
    ],
    db: Annotated[Session, Depends(get_db)],
    _bootstrap: Annotated[None, Depends(ensure_genesis_admin)],
    asset_registry_admin_signature: Annotated[
        str, Header(alias="Asset-Registry-Admin-Signature", min_length=1)
    ],
) -> AssetResponse:
    """Assign a validated PNG immediately using an admin-signed hash commitment."""
    return set_admin_asset_icon(
        db,
        asset_id=asset_id,
        action=request.action,
        icon=request.icon,
        signature=asset_registry_admin_signature,
    )


@router.put(
    "/admin/assets/{asset_id}/annotations",
    response_model=AssetResponse,
    tags=["Admin"],
    operation_id="updateAdminAnnotationsV2",
    summary="Submit a signed admin annotation action for an asset",
)
async def update_admin_annotations_v2(
    asset_id: AssetId,
    _action: Annotated[
        UpdateAdminAnnotationsAction,
        Body(openapi_examples=ADMIN_ANNOTATION_EXAMPLES),
    ],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    _bootstrap: Annotated[None, Depends(ensure_genesis_admin)],
    asset_registry_admin_signature: Annotated[
        str, Header(alias="Asset-Registry-Admin-Signature", min_length=1)
    ],
) -> AssetResponse:
    """Update registry-operated annotations with an authorized signed admin action."""
    return update_admin_annotations(
        db,
        asset_id=asset_id,
        payload=await request.body(),
        signature=asset_registry_admin_signature,
    )


@router.post(
    "/admin/assets/{asset_id}/actions",
    response_model=AssetResponse,
    tags=["Admin"],
    operation_id="submitAdminAssetActionV2",
    summary="Submit a signed asset-scoped admin action",
)
async def submit_admin_asset_action_v2(
    asset_id: AssetId,
    _action: Annotated[AdminAssetAction, Body(openapi_examples=ADMIN_ASSET_EXAMPLES)],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    _bootstrap: Annotated[None, Depends(ensure_genesis_admin)],
    asset_registry_admin_signature: Annotated[
        str, Header(alias="Asset-Registry-Admin-Signature", min_length=1)
    ],
) -> AssetResponse:
    """Apply an authorized signed moderation or icon-review action to an asset."""
    if isinstance(_action, (ApproveIconAction, RejectIconAction)):
        return decide_icon_proposal(
            db,
            asset_id=asset_id,
            payload=await request.body(),
            signature=asset_registry_admin_signature,
        )
    return submit_admin_asset_action(
        db,
        asset_id=asset_id,
        payload=await request.body(),
        signature=asset_registry_admin_signature,
    )
