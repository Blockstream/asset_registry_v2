from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from pydantic_core import to_json
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, load_only, selectinload

from registry_api.constants import IconProposalStatus, Operation
from registry_api.db import SessionLocal
from registry_api.legacy_response import legacy_response_from_asset
from registry_api.models import (
    Action,
    Asset,
    AssetIconProposal,
    AssetSerializedFragment,
)

STREAM_CHUNK_SIZE_BYTES = 256 * 1024
# Passed as an execution option rather than Result.yield_per() so the psycopg
# dialect opens a server-side cursor. Calling yield_per() on an already-executed
# Result only paces how buffered rows are handed out: libpq has by then pulled
# the whole listing into this process, so every in-flight request held a full
# copy of the response in memory.
STREAM_ROW_BATCH = 1000


def refresh_asset_serialized_fragments(db: Session, asset: Asset) -> None:
    fresh_asset = _fresh_asset_by_uuid(db, asset.asset_uuid)
    legacy_json = _json_text(
        legacy_response_from_asset(
            fresh_asset, _legacy_registration_payload(db, fresh_asset)
        )
    )
    v2_json = _json_text(_v2_asset_payload(db, fresh_asset))

    fragment = db.get(AssetSerializedFragment, fresh_asset.asset_uuid)
    if fragment is None:
        db.add(
            AssetSerializedFragment(
                asset_uuid=fresh_asset.asset_uuid,
                legacy_json=legacy_json,
                v2_json=v2_json,
            )
        )
        return

    fragment.legacy_json = legacy_json
    fragment.v2_json = v2_json
    fragment.updated_at = datetime.now(UTC)


def delete_asset_serialized_fragments(db: Session, asset: Asset) -> None:
    db.execute(
        delete(AssetSerializedFragment).where(
            AssetSerializedFragment.asset_uuid == asset.asset_uuid
        )
    )


def legacy_all_json_bytes(db: Session) -> bytes:
    return b"".join(iter_legacy_all_json_bytes(db))


def v2_all_json_bytes(db: Session, *, include_deregistered: bool = False) -> bytes:
    return b"".join(
        iter_v2_all_json_bytes(db, include_deregistered=include_deregistered)
    )


def iter_legacy_all_json_bytes(db: Session) -> Iterator[bytes]:
    return _iter_json_object(_legacy_all_json_chunks(db))


def iter_v2_all_json_bytes(
    db: Session, *, include_deregistered: bool = False
) -> Iterator[bytes]:
    return _iter_json_object(
        _v2_all_json_chunks(db, include_deregistered=include_deregistered)
    )


def stream_legacy_all_json_bytes() -> Iterator[bytes]:
    with SessionLocal() as db:
        yield from _coalesce_bytes(iter_legacy_all_json_bytes(db))


def stream_v2_all_json_bytes(*, include_deregistered: bool = False) -> Iterator[bytes]:
    with SessionLocal() as db:
        yield from _coalesce_bytes(
            iter_v2_all_json_bytes(db, include_deregistered=include_deregistered)
        )


def _legacy_all_json_chunks(db: Session) -> Iterator[tuple[str, str]]:
    rows = db.execute(
        select(Asset.asset_uuid, Asset.asset_id, AssetSerializedFragment.legacy_json)
        .outerjoin(
            AssetSerializedFragment,
            AssetSerializedFragment.asset_uuid == Asset.asset_uuid,
        )
        .where(Asset.status == "active")
        .order_by(Asset.asset_id.asc())
        .execution_options(yield_per=STREAM_ROW_BATCH)
    )

    for asset_uuid, asset_id, fragment_json in rows:
        asset = _fresh_asset_by_uuid(db, asset_uuid) if fragment_json is None else None
        yield (
            asset_id,
            fragment_json
            or _json_text(
                legacy_response_from_asset(
                    asset, _legacy_registration_payload(db, asset)
                )
            ),
        )


def _v2_all_json_chunks(
    db: Session, *, include_deregistered: bool = False
) -> Iterator[tuple[str, str]]:
    query = (
        select(Asset.asset_uuid, Asset.asset_id, AssetSerializedFragment.v2_json)
        .outerjoin(
            AssetSerializedFragment,
            AssetSerializedFragment.asset_uuid == Asset.asset_uuid,
        )
        .order_by(Asset.asset_id.asc())
    )
    if not include_deregistered:
        query = query.where(Asset.status == "active")

    rows = db.execute(query.execution_options(yield_per=STREAM_ROW_BATCH))
    for asset_uuid, asset_id, fragment_json in rows:
        asset = _fresh_asset_by_uuid(db, asset_uuid) if fragment_json is None else None
        yield asset_id, fragment_json or _json_text(_v2_asset_payload(db, asset))


def _iter_json_object(items: Iterator[tuple[str, str]]) -> Iterator[bytes]:
    yield b"{"
    first = True
    for asset_id, payload in items:
        if first:
            first = False
        else:
            yield b","
        yield to_json(asset_id)
        yield b":"
        yield payload.encode("utf-8")
    yield b"}"


def _coalesce_bytes(
    chunks: Iterator[bytes], *, target_size: int = STREAM_CHUNK_SIZE_BYTES
) -> Iterator[bytes]:
    buffer = bytearray()
    for chunk in chunks:
        if len(chunk) >= target_size:
            if buffer:
                yield bytes(buffer)
                buffer.clear()
            yield chunk
            continue

        if len(buffer) + len(chunk) > target_size:
            yield bytes(buffer)
            buffer.clear()

        buffer.extend(chunk)

    if buffer:
        yield bytes(buffer)


def _fresh_asset_by_uuid(db: Session, asset_uuid: Any) -> Asset:
    fresh_asset = db.scalar(
        select(Asset)
        .where(Asset.asset_uuid == asset_uuid)
        .options(*_asset_load_options())
        .execution_options(populate_existing=True)
    )
    if fresh_asset is None:
        raise ValueError("asset row disappeared while reading serialized fragments")
    return fresh_asset


def _asset_load_options() -> tuple[Any, ...]:
    return (
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


def _legacy_registration_payload(db: Session, asset: Asset) -> dict[str, Any] | None:
    action = db.scalar(
        select(Action)
        .where(
            Action.asset_uuid == asset.asset_uuid,
            Action.operation == Operation.LEGACY_REGISTER,
        )
        .order_by(Action.audit_sequence.asc())
        .limit(1)
    )
    payload = action.action if action is not None else None
    if isinstance(payload, dict) and isinstance(payload.get("request"), dict):
        return payload["request"]
    return None


def _v2_asset_payload(db: Session, asset: Asset) -> dict[str, Any]:
    from registry_api.v2_assets import asset_response_from_row

    return asset_response_from_row(db, asset).model_dump(mode="json")


def _json_text(payload: Any) -> str:
    return to_json(payload).decode("utf-8")
