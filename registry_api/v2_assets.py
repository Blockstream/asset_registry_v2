import math
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only, selectinload

from registry_api.asset_registration import (
    new_registered_asset,
    new_registration_action,
)
from registry_api.canonical_json import contract_hash
from registry_api.chain import (
    ChainVerifier,
    IssuanceCommitment,
    TrustingChainVerifier,
    UnconfiguredChainVerifier,
)
from registry_api.constants import IconProposalStatus, Operation
from registry_api.contracts import contract_from_asset, v2_response_contract_from_asset
from registry_api.domain_verification import (
    PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT,
    DomainProof,
    HttpTextFetcher,
    TxtResolver,
    expected_dns_proof,
    expected_http_proof,
    parse_pubkey_bound_domain_proof,
    proof_url,
    verify_pubkey_bound_domain_signature,
)
from registry_api.errors import ErrorCode, RegistryError
from registry_api.models import (
    Action,
    Asset,
    AssetAdminAnnotation,
    AssetCategoryTag,
    AssetCustomAttribute,
    AssetIconProposal,
    AssetMutableMetadata,
    AssetTradingVenue,
    IssuerPubkeyHistory,
)
from registry_api.registration_command import command_from_v2_registration
from registry_api.schemas import (
    AdminActionSummary,
    AdminAnnotations,
    AssetIconDescriptor,
    AssetListResponse,
    AssetResponse,
    IssuerPubkeyHistoryEntry,
    MutableMetadata,
    RegisterAssetRequest,
    TradingVenue,
)
from registry_api.serialized_fragments import (
    refresh_asset_serialized_fragments,
    v2_all_json_bytes,
)
from registry_api.validation import (
    ASSET_TYPES,
    available_trading_venues,
    normalize_asset_id,
    normalize_domain,
    require_case_insensitive_controlled_value,
    require_category_tag,
    require_trading_venue,
)

_SORT_COLUMNS: dict[str, tuple] = {
    "asset_id_asc": (Asset.asset_id.asc(),),
    "domain_asc": (Asset.domain.asc().nulls_last(),),
    "domain_desc": (Asset.domain.desc().nulls_last(),),
    "name_asc": (Asset.name.asc().nulls_last(),),
    "name_desc": (Asset.name.desc().nulls_last(),),
    "ticker_asc": (Asset.ticker.asc().nulls_last(),),
    "ticker_desc": (Asset.ticker.desc().nulls_last(),),
    "created_at_desc": (Asset.created_at.desc(),),
    "updated_at_desc": (Asset.updated_at.desc(),),
}

V2_SORTS = set(_SORT_COLUMNS.keys())


def asset_icon_href(asset_id: str, icon_hash: str) -> str:
    return f"/v2/assets/{asset_id}/icon/{icon_hash}.png"


def register_v2_asset(
    db: Session,
    request: RegisterAssetRequest,
    *,
    enforce_chain_verification: bool = False,
    enforce_domain_verification: bool = False,
    chain_verifier: ChainVerifier | None = None,
    fetch_text: HttpTextFetcher | None = None,
    resolve_txt: TxtResolver | None = None,
    domain_signature: str | None = None,
    registration_payload: bytes | None = None,
) -> AssetResponse:
    method = request.domain_verification_method or "http"
    initial_pubkey = (
        request.contract.initial_issuer_pubkey or request.initial_issuer_pubkey
    )
    if initial_pubkey is None:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "initial issuer pubkey is required",
            status_code=409,
        )

    hash_hex = contract_hash(request.contract.model_dump(exclude_none=True))
    _chain_verifier(
        enforce_chain_verification, chain_verifier
    ).verify_issuance_commitment(
        IssuanceCommitment(asset_id=request.asset_id, contract_hash=hash_hex)
    )

    if enforce_domain_verification:
        _verify_v2_domain_proof(
            DomainProof(
                request.contract.entity.domain,
                request.asset_id,
                request.contract.ticker,
            ),
            method,
            request=request,
            initial_pubkey=initial_pubkey,
            domain_signature=domain_signature,
            registration_payload=registration_payload,
            fetch_text=fetch_text,
            resolve_txt=resolve_txt,
        )

    command = command_from_v2_registration(request, initial_pubkey)
    asset = new_registered_asset(command)

    try:
        db.add(asset)
        db.flush()

        action_payload = {
            "operation": Operation.REGISTER,
            "asset_id": asset.asset_id,
            "contract": request.contract.model_dump(exclude_none=True),
            "domain_verification_method": method,
            "initial_issuer_pubkey": initial_pubkey,
            "initial_issuer_pubkey_source": asset.initial_issuer_pubkey_source,
            "mutable": request.mutable.model_dump(mode="json"),
            "contract_hash": hash_hex,
        }
        action = new_registration_action(
            asset,
            operation=Operation.REGISTER,
            payload=action_payload,
            participates_in_hash_chain=True,
        )
        db.add(action)
        db.flush()

        db.add(
            AssetMutableMetadata(
                asset_uuid=asset.asset_uuid,
                schema_version=1,
                updated_by_action_uuid=action.action_uuid,
            )
        )
        db.add(AssetAdminAnnotation(asset_uuid=asset.asset_uuid))
        db.add(
            IssuerPubkeyHistory(
                asset_uuid=asset.asset_uuid,
                pubkey=asset.current_issuer_pubkey,
                valid_from_action_uuid=action.action_uuid,
            )
        )
        _insert_mutable_rows(db, asset, request.mutable, action.action_uuid)
        db.flush()
        refresh_asset_serialized_fragments(db, asset)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # See if we can get more detailed information about the column that is in conflict
        raise RegistryError(
            ErrorCode.ASSET_CONFLICT,
            "asset is already registered or conflicts with an active namespace",
            {"asset_id": request.asset_id},
            status_code=409,
        ) from exc
    except Exception:
        db.rollback()
        raise

    return get_v2_asset(db, asset.asset_id)


def _verify_v2_domain_proof(
    proof: DomainProof,
    method: str,
    *,
    request: RegisterAssetRequest,
    initial_pubkey: str,
    domain_signature: str | None,
    registration_payload: bytes | None,
    fetch_text: HttpTextFetcher | None = None,
    resolve_txt: TxtResolver | None = None,
) -> None:
    if method == "http":
        if fetch_text is None:
            raise RegistryError(
                ErrorCode.VERIFIER_NOT_CONFIGURED,
                "HTTP domain verifier is not configured",
            )
        domain = normalize_domain(proof.domain)
        body = fetch_text(proof_url(domain, proof.asset_id)).rstrip()
        if _try_verify_pubkey_bound_domain_proof(
            body,
            request=request,
            initial_pubkey=initial_pubkey,
            domain_signature=domain_signature,
            registration_payload=registration_payload,
        ):
            return
        if body == expected_http_proof(domain, proof.asset_id):
            return
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFICATION_FAILED,
            "HTTP domain proof contents did not match",
        )

    if method == "dns":
        if resolve_txt is None:
            raise RegistryError(
                ErrorCode.VERIFIER_NOT_CONFIGURED,
                "DNS domain verifier is not configured",
            )
        domain = normalize_domain(proof.domain)
        records = resolve_txt(domain)
        for record in records:
            if _try_verify_pubkey_bound_domain_proof(
                record.strip(),
                request=request,
                initial_pubkey=initial_pubkey,
                domain_signature=domain_signature,
                registration_payload=registration_payload,
            ):
                return
        if expected_dns_proof(proof.asset_id, proof.ticker) in records:
            return
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFICATION_FAILED, "DNS TXT domain proof was not found"
        )

    raise RegistryError(
        ErrorCode.UNSUPPORTED_DOMAIN_VERIFICATION_METHOD,
        "unsupported domain verification method",
    )


def _try_verify_pubkey_bound_domain_proof(
    content: str,
    *,
    request: RegisterAssetRequest,
    initial_pubkey: str,
    domain_signature: str | None,
    registration_payload: bytes | None,
) -> bool:
    if not _looks_like_pubkey_bound_domain_proof(content):
        return False

    pubkey_proof = parse_pubkey_bound_domain_proof(content)
    if pubkey_proof.pubkey != initial_pubkey:
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFICATION_FAILED,
            "pubkey-bound domain proof pubkey does not match the registration initial issuer pubkey",
            {
                "proof_pubkey": pubkey_proof.pubkey,
                "initial_issuer_pubkey": initial_pubkey,
            },
        )
    if domain_signature is None:
        raise RegistryError(
            ErrorCode.INVALID_SIGNATURE,
            "Asset-Registry-Signature header is required for pubkey-bound domain proof",
            status_code=401,
        )

    verify_pubkey_bound_domain_signature(
        pubkey_proof,
        domain_signature,
        request.contract,
        registration_payload=registration_payload,
    )
    return True


def _looks_like_pubkey_bound_domain_proof(content: str) -> bool:
    fields = {}
    for raw_pair in content.strip().split(","):
        key, separator, value = raw_pair.strip().partition("=")
        if separator == "=":
            fields[key.strip()] = value.strip()
    return fields.get("context") == PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT or (
        "context" in fields and "pubkey" in fields
    )


def get_v2_asset(
    db: Session, asset_id: str, *, include_deregistered: bool = False
) -> AssetResponse:
    try:
        normalized_asset_id = normalize_asset_id(asset_id)
    except ValueError as exc:
        raise RegistryError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    asset = db.scalar(
        _asset_by_id_query(
            normalized_asset_id, include_deregistered=include_deregistered
        )
    )
    if asset is None:
        raise RegistryError(
            ErrorCode.ASSET_NOT_FOUND,
            "asset not found",
            {"asset_id": normalized_asset_id},
            status_code=404,
        )
    return asset_response_from_row(db, asset)


def search_v2_assets(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    sort: str = "asset_id_asc",
    asset_id: str | None = None,
    domain: str | None = None,
    ticker: str | None = None,
    name: str | None = None,
    asset_type: str | None = None,
    category_tag: Sequence[str] | None = None,
    trading_venue: str | None = None,
    created_after: datetime | None = None,
    updated_after: datetime | None = None,
    include_deregistered: bool = False,
) -> AssetListResponse:
    if page < 1:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR, "page must be greater than or equal to 1"
        )
    if page_size < 1 or page_size > 500:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR, "page_size must be between 1 and 500"
        )
    if sort not in V2_SORTS:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR, "unsupported sort", {"sort": sort}
        )

    query = _filtered_asset_query(
        asset_id=asset_id,
        domain=domain,
        ticker=ticker,
        name=name,
        asset_type=asset_type,
        category_tag=category_tag,
        trading_venue=trading_venue,
        created_after=created_after,
        updated_after=updated_after,
        include_deregistered=include_deregistered,
    )
    total_count = (
        db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        or 0
    )
    rows = (
        db.scalars(
            _apply_sort(query, sort).offset((page - 1) * page_size).limit(page_size)
        )
        .unique()
        .all()
    )
    return AssetListResponse(
        items=[asset_response_from_row(db, asset) for asset in rows],
        page=page,
        page_size=page_size,
        total_count=total_count,
        total_pages=math.ceil(total_count / page_size) if total_count else 0,
    )


def all_v2_assets_json_bytes(
    db: Session, *, include_deregistered: bool = False
) -> bytes:
    return v2_all_json_bytes(db, include_deregistered=include_deregistered)


def all_v2_assets_payload(
    db: Session, *, include_deregistered: bool = False
) -> dict[str, dict[str, Any]]:
    rows = (
        db.scalars(
            _apply_sort(
                _filtered_asset_query(include_deregistered=include_deregistered),
                "asset_id_asc",
            )
        )
        .unique()
        .all()
    )
    histories = _issuer_histories(db, [asset.asset_uuid for asset in rows])
    admin_actions = _last_admin_actions(db, rows)
    return {
        asset.asset_id: _asset_response_payload(
            asset,
            issuer_pubkey_history=histories.get(asset.asset_uuid, []),
            last_admin_actions=admin_actions,
        )
        for asset in rows
    }


def asset_response_from_row(db: Session, asset: Asset) -> AssetResponse:
    admin = asset.admin_annotations
    return AssetResponse(
        asset_id=asset.asset_id,
        contract=v2_response_contract_from_asset(asset),
        initial_issuer_pubkey=asset.initial_issuer_pubkey,
        initial_issuer_pubkey_source=asset.initial_issuer_pubkey_source,
        current_issuer_pubkey=asset.current_issuer_pubkey,
        issuer_pubkey_history=_issuer_history(db, asset),
        mutable=MutableMetadata(
            trading_venues=[
                TradingVenue(venue=row.name, url=row.url)
                for row in sorted(
                    asset.trading_venues, key=lambda venue: venue.position
                )
            ],
            category_tags=[
                row.tag
                for row in sorted(asset.category_tags, key=lambda tag: tag.position)
            ],
            custom={
                row.name: row.value
                for row in sorted(asset.custom_attributes, key=lambda attr: attr.name)
            },
        ),
        admin=_admin_annotations(db, admin) if admin is not None else None,
        icon=_icon_descriptor_from_asset(asset),
        status=asset.status,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


def _insert_mutable_rows(
    db: Session, asset: Asset, mutable: MutableMetadata, action_uuid: Any
) -> None:
    for position, venue in enumerate(mutable.trading_venues):
        db.add(
            AssetTradingVenue(
                asset_uuid=asset.asset_uuid,
                name=venue.venue,
                url=venue.url,
                position=position,
                updated_by_action_uuid=action_uuid,
            )
        )
    for position, tag in enumerate(mutable.category_tags):
        db.add(
            AssetCategoryTag(
                asset_uuid=asset.asset_uuid,
                tag=tag,
                position=position,
                updated_by_action_uuid=action_uuid,
            )
        )
    for key, value in mutable.custom.items():
        db.add(
            AssetCustomAttribute(
                asset_uuid=asset.asset_uuid,
                name=key,
                value=value,
                updated_by_action_uuid=action_uuid,
            )
        )


def _chain_verifier(
    enforce_chain_verification: bool, chain_verifier: ChainVerifier | None = None
) -> ChainVerifier:
    if chain_verifier is not None:
        return chain_verifier if enforce_chain_verification else TrustingChainVerifier()
    return (
        UnconfiguredChainVerifier()
        if enforce_chain_verification
        else TrustingChainVerifier()
    )


def _asset_by_id_query(
    asset_id: str, *, include_deregistered: bool = False
) -> Select[tuple[Asset]]:
    normalized_asset_id = normalize_asset_id(asset_id)
    query = _with_asset_children(
        select(Asset).where(Asset.asset_id == normalized_asset_id)
    )
    if not include_deregistered:
        query = query.where(Asset.status == "active")
    return query.order_by(Asset.created_at.desc(), Asset.asset_uuid.desc()).limit(1)


def _filtered_asset_query(
    *,
    asset_id: str | None = None,
    domain: str | None = None,
    ticker: str | None = None,
    name: str | None = None,
    asset_type: str | None = None,
    category_tag: Sequence[str] | None = None,
    trading_venue: str | None = None,
    created_after: datetime | None = None,
    updated_after: datetime | None = None,
    include_deregistered: bool = False,
) -> Select[tuple[Asset]]:
    query = _with_asset_children(select(Asset))
    if not include_deregistered:
        query = query.where(Asset.status == "active")
    if asset_id is not None:
        prefix = asset_id.lower()
        if (
            not prefix
            or len(prefix) > 64
            or any(char not in "0123456789abcdef" for char in prefix)
        ):
            raise RegistryError(
                ErrorCode.VALIDATION_ERROR, "asset_id must be 1-64 hex characters"
            )
        query = query.where(Asset.asset_id.like(f"{prefix}%"))
    if domain is not None:
        try:
            normalized_domain = normalize_domain(domain.lower())
        except ValueError as exc:
            raise RegistryError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
        query = query.where(Asset.domain == normalized_domain)
    if ticker is not None:
        query = query.where(
            func.lower(Asset.ticker).like(
                f"{_escape_like_prefix(ticker.lower())}%", escape="\\"
            )
        )
    if name is not None:
        query = query.where(
            func.lower(Asset.name).like(
                f"{_escape_like_prefix(name.lower())}%", escape="\\"
            )
        )
    if asset_type is not None:
        try:
            normalized_asset_type = require_case_insensitive_controlled_value(
                asset_type, ASSET_TYPES, "asset type"
            )
        except ValueError as exc:
            raise RegistryError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
        query = query.join(AssetAdminAnnotation).where(
            AssetAdminAnnotation.asset_type == normalized_asset_type
        )
    if category_tag:
        try:
            tags = [require_category_tag(tag) for tag in category_tag]
        except ValueError as exc:
            raise RegistryError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
        query = query.where(
            Asset.asset_uuid.in_(
                select(AssetCategoryTag.asset_uuid).where(
                    AssetCategoryTag.tag.in_(tags)
                )
            )
        )
    if trading_venue is not None:
        try:
            venue = require_trading_venue(trading_venue)
        except ValueError as exc:
            raise RegistryError(
                ErrorCode.VALIDATION_ERROR,
                "unsupported trading venue",
                {"available_trading_venues": available_trading_venues()},
            ) from exc
        query = query.where(
            Asset.asset_uuid.in_(
                select(AssetTradingVenue.asset_uuid).where(
                    AssetTradingVenue.name == venue
                )
            )
        )
    if created_after is not None:
        query = query.where(Asset.created_at > created_after)
    if updated_after is not None:
        query = query.where(Asset.updated_at > updated_after)
    return query


def _with_asset_children(query: Select[tuple[Asset]]) -> Select[tuple[Asset]]:
    return query.options(
        selectinload(Asset.trading_venues),
        selectinload(Asset.category_tags),
        selectinload(Asset.custom_attributes),
        selectinload(Asset.admin_annotations),
        selectinload(
            Asset.icon.and_(
                AssetIconProposal.status == IconProposalStatus.APPROVED,
                AssetIconProposal.obsoleted_at.is_(None),
                AssetIconProposal.image_data.is_not(None),
            )
        ).options(
            load_only(
                AssetIconProposal.icon_hash,
                AssetIconProposal.status,
                AssetIconProposal.obsoleted_at,
            )
        ),
    )


def _apply_sort(query: Select[tuple[Asset]], sort: str) -> Select[tuple[Asset]]:
    primary = _SORT_COLUMNS.get(sort, ())
    return query.order_by(*primary, Asset.asset_id.asc(), Asset.asset_uuid.asc())


def _escape_like_prefix(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _issuer_history(db: Session, asset: Asset) -> list[IssuerPubkeyHistoryEntry]:
    valid_until = Action.__table__.alias("valid_until")
    rows = db.execute(
        select(IssuerPubkeyHistory, Action.audit_sequence, valid_until.c.audit_sequence)
        .join(Action, Action.action_uuid == IssuerPubkeyHistory.valid_from_action_uuid)
        .outerjoin(
            valid_until,
            valid_until.c.action_uuid == IssuerPubkeyHistory.valid_until_action_uuid,
        )
        .where(IssuerPubkeyHistory.asset_uuid == asset.asset_uuid)
        .order_by(Action.audit_sequence.asc())
    ).all()
    entries: list[IssuerPubkeyHistoryEntry] = []
    for history, valid_from_audit_id, valid_until_audit_id in rows:
        entries.append(
            IssuerPubkeyHistoryEntry(
                pubkey=history.pubkey,
                valid_from_audit_id=valid_from_audit_id,
                valid_until_audit_id=valid_until_audit_id,
            )
        )
    return entries


def _issuer_histories(
    db: Session, asset_uuids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    if not asset_uuids:
        return {}
    valid_until = Action.__table__.alias("valid_until")
    rows = db.execute(
        select(
            IssuerPubkeyHistory.asset_uuid,
            IssuerPubkeyHistory.pubkey,
            Action.audit_sequence,
            valid_until.c.audit_sequence,
        )
        .join(Action, Action.action_uuid == IssuerPubkeyHistory.valid_from_action_uuid)
        .outerjoin(
            valid_until,
            valid_until.c.action_uuid == IssuerPubkeyHistory.valid_until_action_uuid,
        )
        .where(IssuerPubkeyHistory.asset_uuid.in_(asset_uuids))
        .order_by(IssuerPubkeyHistory.asset_uuid.asc(), Action.audit_sequence.asc())
    ).all()
    histories: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for asset_uuid, pubkey, valid_from_audit_id, valid_until_audit_id in rows:
        histories.setdefault(asset_uuid, []).append(
            {
                "pubkey": pubkey,
                "valid_from_audit_id": valid_from_audit_id,
                "valid_until_audit_id": valid_until_audit_id,
            }
        )
    return histories


def _last_admin_actions(
    db: Session, assets: Sequence[Asset]
) -> dict[uuid.UUID, Action]:
    action_uuids = {
        asset.admin_annotations.last_admin_action_uuid
        for asset in assets
        if asset.admin_annotations is not None
        and asset.admin_annotations.last_admin_action_uuid is not None
    }
    if not action_uuids:
        return {}
    rows = db.scalars(select(Action).where(Action.action_uuid.in_(action_uuids))).all()
    return {action.action_uuid: action for action in rows}


def _asset_response_payload(
    asset: Asset,
    *,
    issuer_pubkey_history: list[dict[str, Any]],
    last_admin_actions: dict[uuid.UUID, Action],
) -> dict[str, Any]:
    payload = {
        "asset_id": asset.asset_id,
        "contract": _contract_payload(asset),
        "initial_issuer_pubkey": asset.initial_issuer_pubkey,
        "initial_issuer_pubkey_source": asset.initial_issuer_pubkey_source,
        "current_issuer_pubkey": asset.current_issuer_pubkey,
        "issuer_pubkey_history": issuer_pubkey_history,
        "mutable": {
            "trading_venues": [
                {"venue": row.name, "url": row.url}
                for row in sorted(
                    asset.trading_venues, key=lambda venue: venue.position
                )
            ],
            "category_tags": [
                row.tag
                for row in sorted(asset.category_tags, key=lambda tag: tag.position)
            ],
            "custom": {
                row.name: row.value
                for row in sorted(asset.custom_attributes, key=lambda attr: attr.name)
            },
        },
        "admin": _admin_annotations_payload(
            asset.admin_annotations, last_admin_actions
        ),
        "status": asset.status,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }
    descriptor = _icon_descriptor_from_asset(asset)
    payload["icon"] = descriptor.model_dump(mode="json") if descriptor else None
    return payload


def _icon_descriptor_from_asset(asset: Asset) -> AssetIconDescriptor | None:
    if (
        asset.icon is None
        or asset.icon.status != IconProposalStatus.APPROVED
        or asset.icon.obsoleted_at is not None
    ):
        return None
    return AssetIconDescriptor(
        href=asset_icon_href(asset.asset_id, asset.icon.icon_hash)
    )


def _contract_payload(asset: Asset) -> dict[str, Any]:
    contract = contract_from_asset(asset)
    contract.setdefault("ticker", None)
    contract.setdefault("initial_issuer_pubkey", None)
    contract.setdefault("issuer_pubkey", None)
    return contract


def _admin_annotations_payload(
    admin: AssetAdminAnnotation | None,
    last_admin_actions: dict[uuid.UUID, Action],
) -> dict[str, Any] | None:
    if admin is None:
        return None
    last_action = None
    if admin.last_admin_action_uuid is not None:
        action = last_admin_actions.get(admin.last_admin_action_uuid)
        if action is not None:
            last_action = {
                "action": action.operation,
                "field": action.action.get("field")
                if isinstance(action.action, dict)
                else None,
                "admin_id": action.admin_id,
                "timestamp": action.server_received_at,
            }
    return {
        "asset_type": admin.asset_type,
        "featured": admin.featured,
        "malicious": admin.malicious,
        "delisted": admin.delisted,
        "admin_notes": admin.admin_notes,
        "last_admin_action": last_action,
    }


def _admin_annotations(db: Session, admin: AssetAdminAnnotation) -> AdminAnnotations:
    last_action = None
    if admin.last_admin_action_uuid is not None:
        action = db.scalar(
            select(Action).where(Action.action_uuid == admin.last_admin_action_uuid)
        )
        if action is not None:
            last_action = AdminActionSummary(
                action=action.operation,
                field=action.action.get("field")
                if isinstance(action.action, dict)
                else None,
                admin_id=action.admin_id,
                timestamp=action.server_received_at,
            )
    return AdminAnnotations(
        asset_type=admin.asset_type,
        featured=admin.featured,
        malicious=admin.malicious,
        delisted=admin.delisted,
        admin_notes=admin.admin_notes,
        last_admin_action=last_action,
    )
