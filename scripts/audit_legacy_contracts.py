from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from registry_api.canonical_json import contract_hash
from registry_api.chain import derive_asset_id


@dataclass(frozen=True)
class ContractAuditFinding:
    asset_id: str
    reason: str


@dataclass
class ContractAuditSummary:
    total: int = 0
    verified: int = 0
    contract_collection_present: int = 0
    contract_collection_absent: int = 0
    top_level_null_collection_only: int = 0
    findings: list[ContractAuditFinding] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.findings and self.verified == self.total


def load_assets(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise TypeError("legacy asset dataset must be an object keyed by asset_id")
    return payload


def audit_legacy_contracts(payload: dict[str, Any]) -> ContractAuditSummary:
    summary = ContractAuditSummary(total=len(payload))
    for asset_id_key, item in payload.items():
        asset_id = str(asset_id_key)
        try:
            _audit_item(asset_id, item, summary)
        except (KeyError, TypeError, ValueError) as exc:
            summary.findings.append(ContractAuditFinding(asset_id, str(exc)))
        else:
            summary.verified += 1
    return summary


def _audit_item(
    asset_id_key: str,
    item: Any,
    summary: ContractAuditSummary,
) -> None:
    if not isinstance(item, dict):
        raise TypeError("asset entry must be an object")
    asset_id = item.get("asset_id", asset_id_key)
    if asset_id != asset_id_key:
        raise ValueError("asset entry key does not match nested asset_id")

    contract = item.get("contract")
    if not isinstance(contract, dict):
        raise TypeError("asset entry contract must be an object")
    if "collection" in contract:
        summary.contract_collection_present += 1
    else:
        summary.contract_collection_absent += 1
        if "collection" in item and item["collection"] is None:
            summary.top_level_null_collection_only += 1

    issuance_prevout = item.get("issuance_prevout")
    if not isinstance(issuance_prevout, dict):
        raise TypeError("asset entry issuance_prevout must be an object")
    prevout_txid = issuance_prevout.get("txid")
    prevout_vout = issuance_prevout.get("vout")
    if not isinstance(prevout_txid, str) or type(prevout_vout) is not int:
        raise TypeError("asset entry issuance_prevout must contain txid and vout")

    hash_hex = contract_hash(contract)
    preserved_hash = item.get("contract_hash")
    if preserved_hash is not None and preserved_hash != hash_hex:
        raise ValueError(
            "canonical contract hash does not match preserved contract_hash"
        )

    derived_asset_id = derive_asset_id(prevout_txid, prevout_vout, hash_hex)
    if derived_asset_id != asset_id:
        raise ValueError(
            "canonical contract and issuance prevout do not derive asset_id"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify legacy canonical contracts against preserved hashes and Liquid "
            "asset IDs."
        )
    )
    parser.add_argument("datasets", nargs="+", type=Path)
    parser.add_argument("--show-findings", action="store_true")
    args = parser.parse_args()

    exit_code = 0
    for path in args.datasets:
        summary = audit_legacy_contracts(load_assets(path))
        print(
            f"{path}: total={summary.total} verified={summary.verified} "
            f"contract_collection_present={summary.contract_collection_present} "
            f"contract_collection_absent={summary.contract_collection_absent} "
            f"top_level_null_collection_only={summary.top_level_null_collection_only} "
            f"findings={len(summary.findings)}"
        )
        if args.show_findings:
            for finding in summary.findings:
                print(f"{finding.asset_id}: {finding.reason}")
        if not summary.valid:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
