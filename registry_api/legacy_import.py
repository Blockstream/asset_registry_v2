from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import wallycore as wally
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from registry_api.db import SessionLocal
from registry_api.errors import ErrorCode, RegistryError
from registry_api.models import Asset
from registry_api.registration import register_legacy_asset
from registry_api.schemas import LegacyAssetRequest
from registry_api.validation import normalize_asset_id

logger = logging.getLogger(__name__)
DEFAULT_PROGRESS_INTERVAL = 1000
DEFAULT_DELAY_SECONDS = 10.0


@dataclass(frozen=True)
class LegacyImportItem:
    asset_id: str
    status: str
    reason: str | None = None


@dataclass
class LegacyImportSummary:
    total: int = 0
    imported: int = 0
    would_import: int = 0
    skipped_existing_asset_id: int = 0
    skipped_namespace_conflict: int = 0
    invalid: int = 0
    failed: int = 0
    items: list[LegacyImportItem] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return self.invalid > 0 or self.failed > 0

    @property
    def successful_migrations(self) -> int:
        return self.imported + self.would_import

    @property
    def failed_migrations(self) -> int:
        return self.total - self.successful_migrations

    def add(self, item: LegacyImportItem) -> None:
        self.items.append(item)
        if item.status == "imported":
            self.imported += 1
        elif item.status == "would_import":
            self.would_import += 1
        elif item.status == "skipped_existing_asset_id":
            self.skipped_existing_asset_id += 1
        elif item.status == "skipped_namespace_conflict":
            self.skipped_namespace_conflict += 1
        elif item.status == "invalid":
            self.invalid += 1
        elif item.status == "failed":
            self.failed += 1


def load_legacy_assets_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("legacy asset import JSON must be an object keyed by asset_id")
    return payload


def import_legacy_assets(
    db: Session,
    payload: dict[str, Any],
    *,
    dry_run: bool = False,
    progress_interval: int = DEFAULT_PROGRESS_INTERVAL,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> LegacyImportSummary:
    summary = LegacyImportSummary(total=len(payload))
    logger.info(
        "starting legacy asset import: total=%s dry_run=%s progress_interval=%s delay_seconds=%s",
        summary.total,
        dry_run,
        progress_interval,
        delay_seconds,
    )

    for processed, (key, value) in enumerate(payload.items(), start=1):
        try:
            request, registered_response = legacy_request_from_listing_item(key, value)
        except (TypeError, ValueError, ValidationError) as exc:
            summary.add(LegacyImportItem(str(key), "invalid", str(exc)))
            _log_progress_if_needed(summary, processed, progress_interval)
            _delay_if_needed(processed, summary.total, progress_interval, delay_seconds, sleep)
            continue

        asset_id = request.asset_id
        namespace_conflict = _active_namespace_conflict(db, request)

        if _asset_id_exists(db, asset_id):
            summary.add(LegacyImportItem(asset_id, "skipped_existing_asset_id"))
            _log_progress_if_needed(summary, processed, progress_interval)
            _delay_if_needed(processed, summary.total, progress_interval, delay_seconds, sleep)
            continue
        if namespace_conflict is not None:
            print(request.contract.entity.domain.lower(), request.contract.ticker)
            summary.add(
                LegacyImportItem(
                    asset_id,
                    "skipped_namespace_conflict",
                    f"active asset {namespace_conflict} already uses domain/ticker namespace",
                )
            )
            _log_progress_if_needed(summary, processed, progress_interval)
            _delay_if_needed(processed, summary.total, progress_interval, delay_seconds, sleep)
            continue
        if dry_run:
            summary.add(LegacyImportItem(asset_id, "would_import", "dry-run"))
            _log_progress_if_needed(summary, processed, progress_interval)
            _delay_if_needed(processed, summary.total, progress_interval, delay_seconds, sleep)
            continue

        try:
            register_legacy_asset(
                db,
                request,
                enforce_chain_verification=False,
                enforce_domain_verification=False,
                make_response=lambda _request, response=registered_response: response,
            )
        except RegistryError as exc:
            if exc.error == ErrorCode.ASSET_CONFLICT:
                summary.add(LegacyImportItem(asset_id, "skipped_namespace_conflict", exc.message))
            else:
                summary.add(LegacyImportItem(asset_id, "failed", exc.message))
        except Exception as exc:
            summary.add(LegacyImportItem(asset_id, "failed", str(exc)))
        else:
            summary.add(LegacyImportItem(asset_id, "imported"))
        _log_progress_if_needed(summary, processed, progress_interval)
        _delay_if_needed(processed, summary.total, progress_interval, delay_seconds, sleep)

    logger.info(
        "finished legacy asset import: total=%s imported=%s would_import=%s "
        "skipped_existing_asset_id=%s skipped_namespace_conflict=%s invalid=%s failed=%s",
        summary.total,
        summary.imported,
        summary.would_import,
        summary.skipped_existing_asset_id,
        summary.skipped_namespace_conflict,
        summary.invalid,
        summary.failed,
    )
    return summary


def _log_progress_if_needed(summary: LegacyImportSummary, processed: int, progress_interval: int) -> None:
    if progress_interval <= 0 or processed % progress_interval != 0:
        return
    logger.info(
        "legacy asset import progress: processed=%s total=%s imported=%s would_import=%s "
        "skipped_existing_asset_id=%s skipped_namespace_conflict=%s invalid=%s failed=%s",
        processed,
        summary.total,
        summary.imported,
        summary.would_import,
        summary.skipped_existing_asset_id,
        summary.skipped_namespace_conflict,
        summary.invalid,
        summary.failed,
    )


def _delay_if_needed(
    processed: int,
    total: int,
    progress_interval: int,
    delay_seconds: float,
    sleep: Callable[[float], None],
) -> None:
    if progress_interval <= 0 or delay_seconds <= 0:
        return
    if processed % progress_interval != 0 or processed >= total:
        return
    logger.info("pausing legacy asset import: processed=%s total=%s delay_seconds=%s", processed, total, delay_seconds)
    sleep(delay_seconds)


def legacy_request_from_listing_item(asset_id_key: str, value: Any) -> tuple[LegacyAssetRequest, dict[str, Any]]:
    if not isinstance(value, dict):
        raise TypeError("legacy asset entry must be an object")

    response = deepcopy(value)
    asset_id = response.get("asset_id", asset_id_key)
    if not isinstance(asset_id, str):
        raise TypeError("legacy asset entry asset_id must be a string")

    normalized_asset_id = normalize_asset_id(asset_id)
    if str(asset_id_key) != normalized_asset_id and response.get("asset_id") is not None:
        normalized_key = normalize_asset_id(str(asset_id_key))
        if normalized_key != normalized_asset_id:
            raise ValueError("legacy asset entry key does not match nested asset_id")

    response["asset_id"] = normalized_asset_id
    contract = response.get("contract")
    if not isinstance(contract, dict):
        raise TypeError("legacy asset entry contract must be an object")
    response["contract"] = _contract_with_legacy_fallbacks(response, contract)
    _compress_legacy_issuer_pubkeys(response)

    request = LegacyAssetRequest.model_validate(response)
    registered_response = deepcopy(response)
    return request, registered_response


def _contract_with_legacy_fallbacks(response: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(contract)
    for field_name in ("entity", "issuer_pubkey", "name", "ticker", "collection", "precision", "version"):
        if field_name not in normalized and field_name in response:
            normalized[field_name] = deepcopy(response[field_name])
    return normalized


def _compress_legacy_issuer_pubkeys(response: dict[str, Any]) -> None:
    contract = response.get("contract")
    if isinstance(contract, dict) and "issuer_pubkey" in contract:
        contract["issuer_pubkey"] = _compressed_pubkey(contract["issuer_pubkey"])
        response["issuer_pubkey"] = contract["issuer_pubkey"]
    elif "issuer_pubkey" in response:
        response["issuer_pubkey"] = _compressed_pubkey(response["issuer_pubkey"])


def _compressed_pubkey(pubkey: Any) -> str:
    if not isinstance(pubkey, str):
        raise TypeError("issuer_pubkey must be a string")
    normalized = pubkey.lower()
    try:
        pubkey_bytes = bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError("issuer_pubkey must be hex") from exc

    if len(pubkey_bytes) == wally.EC_PUBLIC_KEY_LEN and pubkey_bytes[0] in (2, 3):
        wally.ec_public_key_verify(pubkey_bytes)
        return pubkey_bytes.hex()
    if len(pubkey_bytes) == wally.EC_PUBLIC_KEY_UNCOMPRESSED_LEN and pubkey_bytes[0] == 4:
        wally.ec_public_key_verify(pubkey_bytes)
        prefix = b"\x03" if pubkey_bytes[-1] & 1 else b"\x02"
        compressed = prefix + pubkey_bytes[1:33]
        wally.ec_public_key_verify(compressed)
        return compressed.hex()
    raise ValueError("issuer_pubkey must be a compressed or uncompressed secp256k1 public key")


def _asset_id_exists(db: Session, asset_id: str) -> bool:
    return db.scalar(select(Asset.asset_uuid).where(Asset.asset_id == asset_id).limit(1)) is not None


def _active_namespace_conflict(db: Session, request: LegacyAssetRequest) -> str | None:
    if request.contract.ticker is None:
        return None
    ticker = request.contract.ticker
    return db.scalar(
        select(Asset.asset_id)
        .where(
            Asset.status == "active",
            func.lower(Asset.domain) == request.contract.entity.domain.lower(),
            Asset.ticker == ticker,
        )
        .limit(1)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Import legacy v0 asset registry JSON into the configured database.")
    parser.add_argument("json_path", type=Path, help="Path to a legacy all.json-style object keyed by asset_id.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report actions without writing rows.")
    parser.add_argument("--show-items", action="store_true", help="Print one line per input asset.")
    parser.add_argument(
        "--delay-seconds",
        type=_non_negative_float,
        default=DEFAULT_DELAY_SECONDS,
        help="Seconds to pause after each 1000 processed assets. Use 0 to disable. Default: 10.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("legacy asset import script starting: json_path=%s", args.json_path)

    payload = load_legacy_assets_json(args.json_path)
    with SessionLocal() as db:
        summary = import_legacy_assets(db, payload, dry_run=args.dry_run, delay_seconds=args.delay_seconds)

    print(
        "legacy import summary: "
        f"total={summary.total} imported={summary.imported} would_import={summary.would_import} "
        f"skipped_existing_asset_id={summary.skipped_existing_asset_id} "
        f"skipped_namespace_conflict={summary.skipped_namespace_conflict} "
        f"invalid={summary.invalid} failed={summary.failed}"
    )
    if args.show_items:
        for item in summary.items:
            suffix = f" ({item.reason})" if item.reason else ""
            print(f"{item.asset_id}: {item.status}{suffix}")
    print(
        f"successful_migrations={summary.successful_migrations} "
        f"failed_migrations={summary.failed_migrations}"
    )
    return 1 if summary.has_errors else 0


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be greater than or equal to 0")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
