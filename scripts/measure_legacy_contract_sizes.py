#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def canonical_json_size(value: Any) -> int:
    return len(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure legacy asset contract payload sizes.")
    parser.add_argument("json_path", type=Path, help="Path to a legacy all.json-style object keyed by asset_id.")
    parser.add_argument("--top", type=int, default=10, help="Number of largest contracts to print.")
    args = parser.parse_args()

    with args.json_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise SystemExit("expected a JSON object keyed by asset_id")

    largest: list[tuple[int, str, int]] = []
    missing_contracts = 0
    for asset_id, item in payload.items():
        if not isinstance(item, dict) or not isinstance(item.get("contract"), dict):
            missing_contracts += 1
            continue
        contract = item["contract"]
        largest.append((canonical_json_size(contract), str(asset_id), len(contract)))

    largest.sort(reverse=True)
    top = largest[: max(args.top, 0)]
    max_size = top[0][0] if top else 0

    print(f"assets_seen={len(payload)}")
    print(f"contracts_seen={len(largest)}")
    print(f"missing_contracts={missing_contracts}")
    print(f"max_contract_canonical_json_bytes={max_size}")
    print("largest_contracts:")
    for size, asset_id, key_count in top:
        print(f"{size}\tkeys={key_count}\tasset_id={asset_id}")


if __name__ == "__main__":
    main()
