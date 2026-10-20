"""Remove synthesized root null fields from registered contracts.

Revision ID: 0014_preserve_legacy_contract
Revises: 0013_case_insensitive_search
Create Date: 2026-08-27
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from registry_api.canonical_json import contract_hash
from registry_api.chain import derive_asset_id

revision: str = "0014_preserve_legacy_contract"
down_revision: str | None = "0013_case_insensitive_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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

def upgrade() -> None:
    # The legacy importer used to copy top-level fallback fields into the nested
    # contract whenever the top-level key existed. The historical datasets
    # represent absent contract collection and ticker values as top-level JSON
    # nulls, so the importer synthesized root null fields for those assets.
    #
    # Keep top-level compatibility fields, genuine contract nulls, and nulls
    # nested inside an actual contract extra value. Imported legacy registration
    # actions did not participate in the action hash chain, so repair only the
    # actions for which one candidate is proven by the issuance-derived asset ID
    # when available, or otherwise by the preserved contract hash.
    connection = op.get_bind()
    repairs = _legacy_registration_action_repairs(connection)
    if repairs:
        connection.execute(
            sa.text(
                """
                create temporary table legacy_contract_repairs (
                    action_uuid uuid primary key,
                    asset_uuid uuid not null unique,
                    contract jsonb not null,
                    contract_hash text not null
                ) on commit drop
                """
            )
        )
        connection.execute(
            sa.text(
                """
                insert into legacy_contract_repairs (
                    action_uuid,
                    asset_uuid,
                    contract,
                    contract_hash
                )
                values (:action_uuid, :asset_uuid, :contract, :contract_hash)
                """
            ).bindparams(sa.bindparam("contract", type_=postgresql.JSONB)),
            repairs,
        )
        connection.execute(
            sa.text(
                """
                update actions as target
                set action = jsonb_set(
                    jsonb_set(
                        target.action,
                        '{request,contract}',
                        repair.contract
                    ),
                    '{contract_hash}',
                    to_jsonb(repair.contract_hash)
                )
                from legacy_contract_repairs as repair
                where target.action_uuid = repair.action_uuid
                """
            )
        )

    _create_authoritative_registration_contracts(connection)
    _assert_serialized_contract_nulls_are_compatible(connection, "legacy_json")
    _repair_serialized_contract_projection(connection, "legacy_json")
    _assert_serialized_contract_nulls_are_compatible(connection, "v2_json")
    _repair_serialized_contract_projection(connection, "v2_json")
    _assert_contract_extra_nulls_are_compatible(connection)
    _repair_contract_extra_fields(connection)


def _legacy_registration_action_repairs(
    connection: sa.engine.Connection,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        sa.text(
            """
            select action_uuid, asset_uuid, asset_chain_id, action, action_hash
            from actions
            where operation = 'legacy_register'
              and jsonb_typeof(action->'request'->'contract') = 'object'
            order by audit_sequence asc
            """
        )
    ).mappings()

    repairs: list[dict[str, Any]] = []
    for row in rows:
        payload = row["action"]
        imported_item = payload["request"]
        stored_contract = imported_item["contract"]
        current_contract = dict(stored_contract)
        original_issuer_pubkey = TESTNET_LEGACY_UNCOMPRESSED_ISSUER_PUBKEYS.get(
            row["asset_chain_id"]
        )
        if original_issuer_pubkey is not None:
            if "issuer_pubkey" not in current_contract:
                raise RuntimeError(
                    f"legacy registration {row['asset_chain_id']} is missing issuer_pubkey"
                )
            current_contract["issuer_pubkey"] = original_issuer_pubkey
        stripped_contract = {
            key: value for key, value in current_contract.items() if value is not None
        }
        preserved_hash = payload.get("contract_hash")
        if not isinstance(preserved_hash, str):
            raise TypeError(
                f"legacy registration {row['asset_chain_id']} is missing contract_hash"
            )

        if current_contract == stripped_contract:
            if not _candidate_matches_registration(
                imported_item,
                current_contract,
                preserved_hash,
                row["asset_chain_id"],
            ):
                raise RuntimeError(
                    "could not verify registered legacy contract for "
                    f"{row['asset_chain_id']}"
                )
            registered_contract = current_contract
        else:
            current_is_registered = _candidate_matches_registration(
                imported_item,
                current_contract,
                preserved_hash,
                row["asset_chain_id"],
            )
            stripped_is_registered = _candidate_matches_registration(
                imported_item,
                stripped_contract,
                preserved_hash,
                row["asset_chain_id"],
            )
            if current_is_registered == stripped_is_registered:
                raise RuntimeError(
                    "could not uniquely identify registered legacy contract for "
                    f"{row['asset_chain_id']}"
                )
            registered_contract = (
                current_contract if current_is_registered else stripped_contract
            )

        registered_hash = contract_hash(registered_contract)
        if stored_contract != registered_contract or preserved_hash != registered_hash:
            if row["action_hash"] is not None:
                raise RuntimeError(
                    f"cannot repair hash-chained legacy registration {row['asset_chain_id']}"
                )
            repairs.append(
                {
                    "action_uuid": row["action_uuid"],
                    "asset_uuid": row["asset_uuid"],
                    "contract": registered_contract,
                    "contract_hash": registered_hash,
                }
            )
    return repairs


def _candidate_matches_registration(
    imported_item: dict[str, Any],
    candidate_contract: dict[str, Any],
    preserved_hash: str,
    asset_id: str,
) -> bool:
    candidate_hash = contract_hash(candidate_contract)
    issuance_prevout = imported_item.get("issuance_prevout")
    if issuance_prevout is None:
        return candidate_hash == preserved_hash
    if not isinstance(issuance_prevout, dict):
        raise TypeError(f"legacy registration {asset_id} has invalid issuance_prevout")

    prevout_txid = issuance_prevout.get("txid")
    prevout_vout = issuance_prevout.get("vout")
    if not isinstance(prevout_txid, str) or type(prevout_vout) is not int:
        raise TypeError(f"legacy registration {asset_id} has invalid issuance_prevout")
    return derive_asset_id(prevout_txid, prevout_vout, candidate_hash) == asset_id


def _create_authoritative_registration_contracts(
    connection: sa.engine.Connection,
) -> None:
    connection.execute(
        sa.text(
            """
            create temporary table authoritative_registration_contracts
            on commit drop as
            select distinct on (registration.asset_uuid)
                registration.asset_uuid,
                case
                    when registration.operation = 'legacy_register'
                        then registration.action->'request'->'contract'
                    else registration.action->'contract'
                end as contract
            from actions as registration
            where registration.operation in ('legacy_register', 'register')
              and jsonb_typeof(
                  case
                      when registration.operation = 'legacy_register'
                          then registration.action->'request'->'contract'
                      else registration.action->'contract'
                  end
              ) = 'object'
            order by registration.asset_uuid, registration.audit_sequence asc
            """
        )
    )
    connection.execute(
        sa.text(
            """
            create unique index authoritative_registration_contracts_asset_idx
            on authoritative_registration_contracts (asset_uuid)
            """
        )
    )


def _assert_serialized_contract_nulls_are_compatible(
    connection: sa.engine.Connection, column: str
) -> None:
    conflict = (
        connection.execute(
            sa.text(
                f"""
            select asset.asset_id, field.key
            from asset_serialized_fragments as fragment
            join assets as asset on asset.asset_uuid = fragment.asset_uuid
            join authoritative_registration_contracts as registration
              on registration.asset_uuid = fragment.asset_uuid
            cross join lateral jsonb_each(fragment.{column}::jsonb->'contract') as field(key, value)
            where field.value = 'null'::jsonb
              and registration.contract ? field.key
              and registration.contract->field.key <> 'null'::jsonb
            limit 1
            """
            )
        )
        .mappings()
        .first()
    )
    if conflict is not None:
        raise RuntimeError(
            f"serialized contract for {conflict['asset_id']} has null for registered "
            f"field {conflict['key']}"
        )


def _repair_serialized_contract_projection(
    connection: sa.engine.Connection, column: str
) -> None:
    connection.execute(
        sa.text(
            f"""
            update asset_serialized_fragments as fragment
            set {column} = jsonb_set(
                    fragment.{column}::jsonb,
                    '{{contract}}',
                    (
                        select coalesce(jsonb_object_agg(field.key, field.value), '{{}}'::jsonb)
                        from jsonb_each(fragment.{column}::jsonb->'contract') as field(key, value)
                        where field.value <> 'null'::jsonb
                           or registration.contract ? field.key
                    )
                )::text
            from authoritative_registration_contracts as registration
            where fragment.asset_uuid = registration.asset_uuid
              and exists (
                  select 1
                  from jsonb_each(fragment.{column}::jsonb->'contract') as field(key, value)
                  where field.value = 'null'::jsonb
                    and not registration.contract ? field.key
              )
            """
        )
    )


def _assert_contract_extra_nulls_are_compatible(
    connection: sa.engine.Connection,
) -> None:
    conflict = (
        connection.execute(
            sa.text(
                """
            select asset.asset_id, field.key
            from assets as asset
            join authoritative_registration_contracts as registration
              on registration.asset_uuid = asset.asset_uuid
            cross join lateral jsonb_each(asset.contract_extra_fields) as field(key, value)
            where field.value = 'null'::jsonb
              and registration.contract ? field.key
              and registration.contract->field.key <> 'null'::jsonb
            limit 1
            """
            )
        )
        .mappings()
        .first()
    )
    if conflict is not None:
        raise RuntimeError(
            f"contract extras for {conflict['asset_id']} have null for registered "
            f"field {conflict['key']}"
        )


def _repair_contract_extra_fields(connection: sa.engine.Connection) -> None:
    connection.execute(
        sa.text(
            """
            update assets as asset
            set contract_extra_fields = (
                select coalesce(jsonb_object_agg(field.key, field.value), '{}'::jsonb)
                from jsonb_each(asset.contract_extra_fields) as field(key, value)
                where field.value <> 'null'::jsonb
                   or registration.contract ? field.key
            )
            from authoritative_registration_contracts as registration
            where asset.asset_uuid = registration.asset_uuid
              and exists (
                  select 1
                  from jsonb_each(asset.contract_extra_fields) as field(key, value)
                  where field.value = 'null'::jsonb
                    and not registration.contract ? field.key
              )
            """
        )
    )


def downgrade() -> None:
    # The removed key was not part of the registered contract, so restoring it
    # would recreate invalid data.
    pass
