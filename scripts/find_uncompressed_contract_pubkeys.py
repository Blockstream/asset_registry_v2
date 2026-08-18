#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ASSET_ID_RE = re.compile(r"^[0-9a-fA-F]{64}$")
UNCOMPRESSED_PUBKEY_RE = re.compile(r"^04[0-9a-fA-F]{128}$")


@dataclass(frozen=True)
class UncompressedIssuerPubkey:
    asset_id: str
    pubkey: str


@dataclass(frozen=True)
class ScanSummary:
    assets_seen: int
    uncompressed_pubkeys: int
    missing_contracts: int
    missing_contract_issuer_pubkeys: int
    non_string_contract_issuer_pubkeys: int
    invalid_asset_ids: int


def load_assets(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object keyed by asset_id")
    return payload


def find_uncompressed_contract_pubkeys(
    payload: dict[str, Any],
) -> tuple[list[UncompressedIssuerPubkey], ScanSummary]:
    findings: list[UncompressedIssuerPubkey] = []
    missing_contracts = 0
    missing_contract_issuer_pubkeys = 0
    non_string_contract_issuer_pubkeys = 0
    invalid_asset_ids = 0

    for asset_id_key, item in payload.items():
        asset_id = str(asset_id_key).lower()
        if not ASSET_ID_RE.fullmatch(asset_id):
            invalid_asset_ids += 1

        if not isinstance(item, dict) or not isinstance(item.get("contract"), dict):
            missing_contracts += 1
            continue

        contract = item["contract"]
        if "issuer_pubkey" not in contract:
            missing_contract_issuer_pubkeys += 1
            continue

        issuer_pubkey = contract["issuer_pubkey"]
        if not isinstance(issuer_pubkey, str):
            non_string_contract_issuer_pubkeys += 1
            continue

        if UNCOMPRESSED_PUBKEY_RE.fullmatch(issuer_pubkey):
            findings.append(UncompressedIssuerPubkey(asset_id, issuer_pubkey.lower()))

    summary = ScanSummary(
        assets_seen=len(payload),
        uncompressed_pubkeys=len(findings),
        missing_contracts=missing_contracts,
        missing_contract_issuer_pubkeys=missing_contract_issuer_pubkeys,
        non_string_contract_issuer_pubkeys=non_string_contract_issuer_pubkeys,
        invalid_asset_ids=invalid_asset_ids,
    )
    return findings, summary


def write_csv(path: Path, findings: list[UncompressedIssuerPubkey], *, include_header: bool = False) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        if include_header:
            writer.writerow(["asset_id", "uncompressed_pubkey"])
        for finding in findings:
            writer.writerow([finding.asset_id, finding.pubkey])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find legacy assets whose contract.issuer_pubkey is an uncompressed secp256k1 public key."
    )
    parser.add_argument("json_path", type=Path, help="Path to a legacy all.json-style object keyed by asset_id.")
    parser.add_argument(
        "csv_path",
        nargs="?",
        type=Path,
        default=Path("uncompressed_contract_issuer_pubkeys.csv"),
        help="CSV output path. Default: uncompressed_contract_issuer_pubkeys.csv",
    )
    parser.add_argument("--include-header", action="store_true", help="Include a CSV header row.")
    args = parser.parse_args()

    findings, summary = find_uncompressed_contract_pubkeys(load_assets(args.json_path))
    write_csv(args.csv_path, findings, include_header=args.include_header)

    print(f"assets_seen={summary.assets_seen}")
    print(f"uncompressed_contract_issuer_pubkeys={summary.uncompressed_pubkeys}")
    print(f"missing_contracts={summary.missing_contracts}")
    print(f"missing_contract_issuer_pubkeys={summary.missing_contract_issuer_pubkeys}")
    print(f"non_string_contract_issuer_pubkeys={summary.non_string_contract_issuer_pubkeys}")
    print(f"invalid_asset_ids={summary.invalid_asset_ids}")
    print(f"csv_path={args.csv_path}")


if __name__ == "__main__":
    main()
