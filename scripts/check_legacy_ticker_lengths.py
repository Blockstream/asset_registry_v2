#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TickerFinding:
    asset_id: str
    ticker: str

    @property
    def length(self) -> int:
        return len(self.ticker)


def iter_tickers(payload: dict[str, Any]) -> tuple[list[TickerFinding], int, int, int]:
    findings: list[TickerFinding] = []
    missing_ticker = 0
    null_ticker = 0
    non_string_ticker = 0

    for asset_id, item in payload.items():
        if not isinstance(item, dict) or "ticker" not in item:
            missing_ticker += 1
            continue

        ticker = item["ticker"]
        if ticker is None:
            null_ticker += 1
            continue
        if not isinstance(ticker, str):
            non_string_ticker += 1
            continue

        findings.append(TickerFinding(str(asset_id), ticker))

    return findings, missing_ticker, null_ticker, non_string_ticker


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report legacy all.json asset tickers longer than selected limits."
    )
    parser.add_argument("json_path", type=Path, help="Path to a legacy all.json-style object keyed by asset_id.")
    parser.add_argument("--limits", type=int, nargs="+", default=[32, 24], help="Ticker lengths to check.")
    parser.add_argument("--top", type=int, default=20, help="Number of longest tickers to print.")
    args = parser.parse_args()

    with args.json_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise SystemExit("expected a JSON object keyed by asset_id")

    tickers, missing_ticker, null_ticker, non_string_ticker = iter_tickers(payload)
    tickers.sort(key=lambda finding: (finding.length, finding.asset_id), reverse=True)

    print(f"assets_seen={len(payload)}")
    print(f"tickers_seen={len(tickers)}")
    print(f"missing_ticker={missing_ticker}")
    print(f"null_ticker={null_ticker}")
    print(f"non_string_ticker={non_string_ticker}")
    print(f"max_ticker_length={tickers[0].length if tickers else 0}")

    for limit in args.limits:
        over_limit = [finding for finding in tickers if finding.length > limit]
        print(f"tickers_over_{limit}={len(over_limit)}")

    longest = tickers[: max(args.top, 0)]
    if longest:
        print("longest_tickers:")
        for finding in longest:
            print(f"{finding.length}\t{finding.ticker}\t{finding.asset_id}")


if __name__ == "__main__":
    main()
