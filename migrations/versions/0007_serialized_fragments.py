"""Add cached serialized asset fragments.

Revision ID: 0007_serialized_fragments
Revises: 0006_case_sensitive_ticker
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_serialized_fragments"
down_revision: str | None = "0006_case_sensitive_ticker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_serialized_fragments",
        sa.Column("asset_uuid", sa.UUID(), nullable=False),
        sa.Column("legacy_json", sa.Text(), nullable=False),
        sa.Column("v2_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["asset_uuid"], ["assets.asset_uuid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("asset_uuid"),
    )
    op.create_index("asset_serialized_fragments_asset_idx", "asset_serialized_fragments", ["asset_uuid"])
    _backfill_serialized_fragments()


def downgrade() -> None:
    op.drop_index("asset_serialized_fragments_asset_idx", table_name="asset_serialized_fragments")
    op.drop_table("asset_serialized_fragments")


def _backfill_serialized_fragments() -> None:
    # Use only columns available at this revision. Live ORM models and response
    # serializers also query icon and issuance columns added by later revisions.
    op.execute("""
        WITH projected AS (
            SELECT a.*,
                jsonb_build_object(
                    'entity', jsonb_build_object('domain', a.domain),
                    'name', a.name, 'precision', a.precision, 'version', a.contract_version
                ) || jsonb_build_object(
                    CASE WHEN a.initial_issuer_pubkey_source = 'contract'
                         THEN 'initial_issuer_pubkey' ELSE 'issuer_pubkey' END,
                    a.initial_issuer_pubkey
                ) || CASE WHEN coalesce(a.ticker, '') <> ''
                          THEN jsonb_build_object('ticker', a.ticker) ELSE '{}'::jsonb END
                  || (a.contract_extra_fields - ARRAY[
                      'entity', 'name', 'precision', 'version', 'ticker',
                      'initial_issuer_pubkey', 'issuer_pubkey'
                  ]) AS contract
            FROM assets a
        ), payloads AS (
            SELECT a.asset_uuid, a.asset_id,
                coalesce(saved.request,
                    jsonb_build_object(
                        'asset_id', a.asset_id, 'contract', a.contract,
                        'version', a.contract_version, 'name', a.name,
                        'precision', a.precision, 'entity', jsonb_build_object('domain', a.domain)
                    ) || CASE WHEN a.initial_issuer_pubkey_source <> 'contract'
                              THEN jsonb_build_object('issuer_pubkey', a.initial_issuer_pubkey)
                              ELSE '{}'::jsonb END
                      || CASE WHEN coalesce(a.ticker, '') <> ''
                              THEN jsonb_build_object('ticker', a.ticker) ELSE '{}'::jsonb END
                ) || jsonb_build_object('asset_id', a.asset_id) AS legacy,
                jsonb_build_object(
                    'asset_id', a.asset_id, 'contract', a.contract,
                    'initial_issuer_pubkey', a.initial_issuer_pubkey,
                    'initial_issuer_pubkey_source', a.initial_issuer_pubkey_source,
                    'current_issuer_pubkey', a.current_issuer_pubkey,
                    'issuer_pubkey_history', coalesce((
                        SELECT jsonb_agg(jsonb_build_object(
                            'pubkey', h.pubkey, 'valid_from_audit_id', first_action.audit_sequence,
                            'valid_until_audit_id', last_action.audit_sequence
                        ) ORDER BY first_action.audit_sequence)
                        FROM issuer_pubkey_history h
                        JOIN actions first_action ON first_action.action_uuid = h.valid_from_action_uuid
                        LEFT JOIN actions last_action ON last_action.action_uuid = h.valid_until_action_uuid
                        WHERE h.asset_uuid = a.asset_uuid
                    ), '[]'::jsonb),
                    'mutable', jsonb_build_object(
                        'trading_venues', coalesce((
                            SELECT jsonb_agg(jsonb_build_object('venue', name, 'url', url) ORDER BY position)
                            FROM asset_trading_venues WHERE asset_uuid = a.asset_uuid
                        ), '[]'::jsonb),
                        'category_tags', coalesce((
                            SELECT jsonb_agg(tag ORDER BY position)
                            FROM asset_category_tags WHERE asset_uuid = a.asset_uuid
                        ), '[]'::jsonb),
                        'custom', coalesce((
                            SELECT jsonb_object_agg(name, value)
                            FROM asset_custom_attributes WHERE asset_uuid = a.asset_uuid
                        ), '{}'::jsonb)
                    ),
                    'admin', CASE WHEN admin.asset_uuid IS NULL THEN NULL ELSE jsonb_build_object(
                        'asset_type', admin.asset_type, 'featured', admin.featured,
                        'malicious', admin.malicious, 'delisted', admin.delisted,
                        'admin_notes', admin.admin_notes,
                        'last_admin_action', CASE WHEN last_admin.action_uuid IS NULL THEN NULL
                            ELSE jsonb_build_object(
                                'action', last_admin.operation, 'field', last_admin.action->'field',
                                'admin_id', last_admin.admin_id, 'timestamp', last_admin.server_received_at
                            ) END
                    ) END,
                    'status', a.status, 'created_at', a.created_at, 'updated_at', a.updated_at
                ) AS v2
            FROM projected a
            LEFT JOIN LATERAL (
                SELECT action->'request' AS request FROM actions
                WHERE asset_uuid = a.asset_uuid AND operation = 'legacy_register'
                  AND jsonb_typeof(action->'request') = 'object'
                ORDER BY audit_sequence ASC LIMIT 1
            ) saved ON true
            LEFT JOIN asset_admin_annotations admin ON admin.asset_uuid = a.asset_uuid
            LEFT JOIN actions last_admin ON last_admin.action_uuid = admin.last_admin_action_uuid
        )
        INSERT INTO asset_serialized_fragments (asset_uuid, legacy_json, v2_json)
        SELECT asset_uuid,
            CASE WHEN asset_id IN (
                '92ac106ea13e65fe1ccae10ca69d6bd14147bb445f4c6119b8db5d027c543d15',
                '95b3d6bba96c0610e058fe5aa31ac7d3861279528d1550ee6ce9237118078a61',
                'e4c76430b56dbd0522f4cf77cb0fe4e652f7b7ac1a2cacac0d336021405a3404',
                '88867e59f6aef276706b4253085cadd38ef9836e2e8c0b94bb482f73d07e551e'
            ) THEN jsonb_set(jsonb_set(legacy, '{issuer_pubkey}', to_jsonb(
                '04d8bb0680c89a19042a4c30f52ebb334dc075bcd53191e4de3885faa0eb6437db56a4e71286ab497274361ef558d4c69eaacdb3eacbe7a469f25de95636db4117'::text
            )), '{contract,issuer_pubkey}', to_jsonb(
                '04d8bb0680c89a19042a4c30f52ebb334dc075bcd53191e4de3885faa0eb6437db56a4e71286ab497274361ef558d4c69eaacdb3eacbe7a469f25de95636db4117'::text
            )) ELSE legacy END::text,
            v2::text
        FROM payloads
    """)
