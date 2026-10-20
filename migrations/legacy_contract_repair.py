"""Frozen data formats shared by revisions 0014 and 0016.

Keep this independent of application models, validators, and serializers.
Unprovable historical data is left intact and reported for operator repair.
"""

import json
import logging
import re
from copy import deepcopy
from itertools import product

import sqlalchemy as sa
import wallycore as wally
from sqlalchemy.dialects import postgresql

logger = logging.getLogger("alembic.runtime.migration")
BATCH_SIZE = 500
KNOWN_CONTRACT_FIELDS = {
    "entity",
    "name",
    "precision",
    "version",
    "ticker",
    "issuer_pubkey",
    "initial_issuer_pubkey",
}
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


def _canonical(contract: dict) -> str:
    return json.dumps(
        contract,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def fragment_json(payload: dict) -> str:
    # Match the compact UTF-8 text written by the live fragment serializer.
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _contract_hash(contract: dict) -> str:
    return bytes(wally.sha256(_canonical(contract).encode("utf-8")))[::-1].hex()


def _registered_contract(payload: dict, asset_id: str) -> dict:
    request = payload["request"]
    stored = dict(request["contract"])
    original_key = TESTNET_LEGACY_UNCOMPRESSED_ISSUER_PUBKEYS.get(asset_id)
    if original_key is not None:
        if "issuer_pubkey" not in stored:
            raise ValueError("missing issuer_pubkey")
        stored["issuer_pubkey"] = original_key

    prevout = request.get("issuance_prevout")
    if prevout is not None:
        if not isinstance(prevout, dict):
            raise ValueError("invalid issuance_prevout")
        txid, vout = prevout.get("txid"), prevout.get("vout")
        if (
            not isinstance(txid, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", txid) is None
            or type(vout) is not int
            or not 0 <= vout <= 4294967295
        ):
            raise ValueError("invalid issuance_prevout")

    # The old importer synthesized only collection/ticker fallback nulls.
    # Include their subsets, so genuine custom nulls can survive the repair.
    # Top-level nulls can also recover these two keys after the original 0014
    # erased them. No other missing field can safely be invented.
    candidates = {}
    for base in (stored, {k: v for k, v in stored.items() if v is not None}):
        optional = [
            key
            for key in ("collection", "ticker")
            if (key in base and base[key] is None)
            or (key not in base and key in request and request[key] is None)
        ]
        for present in product((False, True), repeat=len(optional)):
            candidate = dict(base)
            for key, include in zip(optional, present):
                if include:
                    candidate[key] = None
                else:
                    candidate.pop(key, None)
            candidates[_canonical(candidate)] = candidate

    matches = []
    for candidate in candidates.values():
        hash_hex = _contract_hash(candidate)
        if prevout is None:
            matches_registration = hash_hex == payload.get("contract_hash")
        else:
            entropy = wally.tx_elements_issuance_generate_entropy(
                bytes.fromhex(prevout["txid"])[::-1],
                prevout["vout"],
                bytes.fromhex(hash_hex)[::-1],
            )
            derived = bytes(wally.tx_elements_issuance_calculate_asset(entropy))[
                ::-1
            ].hex()
            matches_registration = derived == asset_id
        if matches_registration:
            matches.append(candidate)
    if len(matches) != 1:
        raise ValueError("could not uniquely identify registered legacy contract")
    return matches[0]


def _repair_nulls(projected: dict, registered: dict) -> dict:
    repaired = dict(projected)
    for key, value in list(repaired.items()):
        if value is None:
            if key not in registered:
                del repaired[key]
            else:
                repaired[key] = registered[key]
    for key, value in registered.items():
        if value is None:
            repaired[key] = None
    return repaired


def repair_legacy_contracts(connection: sa.engine.Connection) -> None:
    after = None
    repaired_count = skipped_count = 0
    while True:
        rows = (
            connection.execute(
                sa.text("""
            SELECT a.action_uuid, asset.asset_uuid, a.asset_chain_id, a.operation,
                   a.action, a.action_hash, asset.contract_extra_fields,
                   f.legacy_json, f.v2_json
            FROM assets asset
            JOIN LATERAL (
                SELECT action_uuid, asset_chain_id, operation, action, action_hash
                FROM actions
                WHERE asset_uuid = asset.asset_uuid
                  AND operation IN ('legacy_register', 'register')
                ORDER BY audit_sequence LIMIT 1
            ) a ON true
            LEFT JOIN asset_serialized_fragments f ON f.asset_uuid = asset.asset_uuid
            WHERE CAST(:after AS uuid) IS NULL OR asset.asset_uuid > CAST(:after AS uuid)
            ORDER BY asset.asset_uuid LIMIT :batch_size
        """),
                {"after": after, "batch_size": BATCH_SIZE},
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        action_updates, extra_updates, fragment_updates = [], [], []
        for row in rows:
            try:
                payload = row["action"]
                if row["operation"] == "legacy_register":
                    contract = _registered_contract(payload, row["asset_chain_id"])
                    corrected = deepcopy(payload)
                    corrected["request"]["contract"] = contract
                    corrected["contract_hash"] = _contract_hash(contract)
                    if corrected != payload:
                        if row["action_hash"] is not None:
                            raise ValueError(
                                "cannot modify a hash-chained registration"
                            )
                        action_updates.append(
                            {"uuid": row["action_uuid"], "action": corrected}
                        )
                else:
                    contract = payload["contract"]
                if not isinstance(contract, dict):
                    raise TypeError("missing registration contract")
            except (KeyError, TypeError, ValueError) as exc:
                skipped_count += 1
                logger.warning(
                    "Skipping legacy contract repair for %s: %s",
                    row["asset_chain_id"],
                    exc,
                )
                continue

            registered_extras = {
                k: v for k, v in contract.items() if k not in KNOWN_CONTRACT_FIELDS
            }
            extras = _repair_nulls(row["contract_extra_fields"], registered_extras)
            if extras != row["contract_extra_fields"]:
                extra_updates.append({"uuid": row["asset_uuid"], "extras": extras})
            fragment = {"uuid": row["asset_uuid"]}
            changed = False
            for column in ("legacy_json", "v2_json"):
                original = row[column]
                fragment[column] = original
                if original is None:
                    continue
                saved = json.loads(original)
                projected = saved.get("contract")
                if not isinstance(projected, dict):
                    continue
                repaired = _repair_nulls(projected, contract)
                if (
                    column == "legacy_json"
                    and row["asset_chain_id"]
                    in TESTNET_LEGACY_UNCOMPRESSED_ISSUER_PUBKEYS
                ):
                    repaired["issuer_pubkey"] = contract["issuer_pubkey"]
                if repaired != projected:
                    saved["contract"] = repaired
                    fragment[column] = fragment_json(saved)
                    changed = True
            if changed:
                fragment_updates.append(fragment)

        if action_updates:
            connection.execute(
                sa.text(
                    "UPDATE actions SET action = :action WHERE action_uuid = :uuid"
                ).bindparams(sa.bindparam("action", type_=postgresql.JSONB)),
                action_updates,
            )
        if extra_updates:
            connection.execute(
                sa.text(
                    "UPDATE assets SET contract_extra_fields = :extras WHERE asset_uuid = :uuid"
                ).bindparams(sa.bindparam("extras", type_=postgresql.JSONB)),
                extra_updates,
            )
        if fragment_updates:
            connection.execute(
                sa.text("""
                UPDATE asset_serialized_fragments SET legacy_json = :legacy_json, v2_json = :v2_json
                WHERE asset_uuid = :uuid
            """),
                fragment_updates,
            )
        repaired_count += len(action_updates)
        after = rows[-1]["asset_uuid"]
    logger.info(
        "Legacy contract repair: repaired_actions=%s skipped=%s",
        repaired_count,
        skipped_count,
    )
