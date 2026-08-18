"""Initial asset registry v2 schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-04-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("create extension if not exists pgcrypto")

    op.create_table(
        "assets",
        sa.Column("asset_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("precision", sa.SmallInteger(), nullable=False),
        sa.Column("domain_verification_method", sa.Text(), server_default="http", nullable=False),
        sa.Column("initial_issuer_pubkey", sa.String(length=66), nullable=False),
        sa.Column("initial_issuer_pubkey_source", sa.Text(), nullable=False),
        sa.Column("current_issuer_pubkey", sa.String(length=66), nullable=False),
        sa.Column("mutable_schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("asset_id ~ '^[0-9a-f]{64}$'", name="assets_asset_id_format_chk"),
        sa.CheckConstraint("initial_issuer_pubkey ~ '^(02|03)[0-9a-f]{64}$'", name="assets_initial_pubkey_format_chk"),
        sa.CheckConstraint("current_issuer_pubkey ~ '^(02|03)[0-9a-f]{64}$'", name="assets_current_pubkey_format_chk"),
        sa.CheckConstraint("contract_version >= 0", name="assets_contract_version_chk"),
        sa.CheckConstraint("mutable_schema_version >= 1", name="assets_mutable_schema_version_chk"),
        sa.PrimaryKeyConstraint("asset_uuid"),
    )

    op.create_table(
        "actions",
        sa.Column("action_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("audit_sequence", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("asset_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_chain_id", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("action", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("nonce", sa.Text(), nullable=True),
        sa.Column("issuer_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_pubkey", sa.String(length=66), nullable=True),
        sa.Column("admin_id", sa.Text(), nullable=True),
        sa.Column("server_received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("asset_chain_id ~ '^[0-9a-f]{64}$'", name="actions_asset_chain_id_format_chk"),
        sa.CheckConstraint("verified_pubkey is null or verified_pubkey ~ '^(02|03)[0-9a-f]{64}$'", name="actions_verified_pubkey_format_chk"),
        sa.ForeignKeyConstraint(["asset_uuid"], ["assets.asset_uuid"]),
        sa.PrimaryKeyConstraint("action_uuid"),
        sa.UniqueConstraint("audit_sequence"),
    )

    op.create_table(
        "asset_mutable_metadata",
        sa.Column("mutable_metadata_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("asset_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_action_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("schema_version >= 1", name="asset_mutable_metadata_schema_version_chk"),
        sa.ForeignKeyConstraint(["asset_uuid"], ["assets.asset_uuid"]),
        sa.ForeignKeyConstraint(["updated_by_action_uuid"], ["actions.action_uuid"]),
        sa.PrimaryKeyConstraint("mutable_metadata_uuid"),
        sa.UniqueConstraint("asset_uuid"),
    )
    op.create_table(
        "asset_trading_venues",
        sa.Column("trading_venue_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("asset_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_action_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["asset_uuid"], ["assets.asset_uuid"]),
        sa.ForeignKeyConstraint(["updated_by_action_uuid"], ["actions.action_uuid"]),
        sa.PrimaryKeyConstraint("trading_venue_uuid"),
        sa.UniqueConstraint("asset_uuid", "name", "url", name="asset_trading_venues_asset_name_url_uidx"),
        sa.UniqueConstraint("asset_uuid", "url", name="asset_trading_venues_asset_url_uidx"),
    )
    op.create_table(
        "asset_category_tags",
        sa.Column("category_tag_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("asset_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_action_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["asset_uuid"], ["assets.asset_uuid"]),
        sa.ForeignKeyConstraint(["updated_by_action_uuid"], ["actions.action_uuid"]),
        sa.PrimaryKeyConstraint("category_tag_uuid"),
        sa.UniqueConstraint("asset_uuid", "tag", name="asset_category_tags_asset_tag_uidx"),
    )
    op.create_table(
        "asset_custom_attributes",
        sa.Column("custom_attribute_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("asset_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_action_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["asset_uuid"], ["assets.asset_uuid"]),
        sa.ForeignKeyConstraint(["updated_by_action_uuid"], ["actions.action_uuid"]),
        sa.PrimaryKeyConstraint("custom_attribute_uuid"),
        sa.UniqueConstraint("asset_uuid", "name", name="asset_custom_attributes_asset_name_uidx"),
    )
    op.create_table(
        "asset_admin_annotations",
        sa.Column("admin_annotation_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("asset_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_type", sa.Text(), nullable=True),
        sa.Column("featured", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("malicious", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("delisted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column("last_admin_action_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_admin_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["asset_uuid"], ["assets.asset_uuid"]),
        sa.ForeignKeyConstraint(["last_admin_action_uuid"], ["actions.action_uuid"]),
        sa.PrimaryKeyConstraint("admin_annotation_uuid"),
        sa.UniqueConstraint("asset_uuid"),
    )
    op.create_table(
        "issuer_pubkey_history",
        sa.Column("issuer_pubkey_history_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("asset_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pubkey", sa.String(length=66), nullable=False),
        sa.Column("valid_from_action_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("valid_until_action_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("pubkey ~ '^(02|03)[0-9a-f]{64}$'", name="issuer_pubkey_history_pubkey_format_chk"),
        sa.ForeignKeyConstraint(["asset_uuid"], ["assets.asset_uuid"]),
        sa.ForeignKeyConstraint(["valid_from_action_uuid"], ["actions.action_uuid"]),
        sa.ForeignKeyConstraint(["valid_until_action_uuid"], ["actions.action_uuid"]),
        sa.PrimaryKeyConstraint("issuer_pubkey_history_uuid"),
    )

    op.create_index("assets_asset_id_idx", "assets", ["asset_id"])
    op.create_index("assets_asset_id_prefix_idx", "assets", ["asset_id"], postgresql_ops={"asset_id": "text_pattern_ops"})
    op.create_index("assets_created_at_idx", "assets", [sa.text("created_at desc"), "asset_uuid"])
    op.create_index("assets_domain_idx", "assets", ["domain"])
    op.create_index("assets_domain_ticker_active_uidx", "assets", [sa.text("lower(domain)"), sa.text("lower(ticker)")], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_index("assets_name_idx", "assets", ["name"], postgresql_ops={"name": "text_pattern_ops"})
    op.create_index("assets_one_active_asset_id_uidx", "assets", ["asset_id"], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_index("assets_ticker_idx", "assets", ["ticker"], postgresql_ops={"ticker": "text_pattern_ops"})
    op.create_index("assets_updated_at_idx", "assets", [sa.text("updated_at desc"), "asset_uuid"])
    op.create_index("actions_actor_sequence_idx", "actions", ["actor", "audit_sequence"])
    op.create_index("actions_asset_sequence_idx", "actions", ["asset_uuid", "audit_sequence"])
    op.create_index("actions_chain_asset_sequence_idx", "actions", ["asset_chain_id", "audit_sequence"])
    op.create_index("actions_issuer_nonce_uidx", "actions", ["asset_uuid", "nonce"], unique=True, postgresql_where=sa.text("actor = 'issuer'"))
    op.create_index("actions_operation_sequence_idx", "actions", ["operation", "audit_sequence"])
    op.create_index("actions_received_at_idx", "actions", ["server_received_at", "audit_sequence"])
    op.create_index("asset_mutable_metadata_action_idx", "asset_mutable_metadata", ["updated_by_action_uuid"])
    op.create_index("asset_trading_venues_asset_position_idx", "asset_trading_venues", ["asset_uuid", "position"])
    op.create_index("asset_trading_venues_name_idx", "asset_trading_venues", ["name", "asset_uuid"])
    op.create_index("asset_category_tags_asset_position_idx", "asset_category_tags", ["asset_uuid", "position"])
    op.create_index("asset_category_tags_tag_idx", "asset_category_tags", ["tag", "asset_uuid"])
    op.create_index("asset_custom_attributes_name_idx", "asset_custom_attributes", ["name", "asset_uuid"])
    op.create_index("asset_custom_attributes_value_gin_idx", "asset_custom_attributes", ["value"], postgresql_using="gin", postgresql_ops={"value": "jsonb_path_ops"})
    op.create_index("asset_admin_asset_type_idx", "asset_admin_annotations", ["asset_type"])
    op.create_index("asset_admin_delisted_idx", "asset_admin_annotations", ["delisted"], postgresql_where=sa.text("delisted = true"))
    op.create_index("asset_admin_featured_idx", "asset_admin_annotations", ["featured"], postgresql_where=sa.text("featured = true"))
    op.create_index("asset_admin_last_action_idx", "asset_admin_annotations", ["last_admin_action_uuid"])
    op.create_index("asset_admin_malicious_idx", "asset_admin_annotations", ["malicious"], postgresql_where=sa.text("malicious = true"))
    op.create_index("issuer_pubkey_history_asset_idx", "issuer_pubkey_history", ["asset_uuid", "created_at"])
    op.create_index("issuer_pubkey_history_one_current_uidx", "issuer_pubkey_history", ["asset_uuid"], unique=True, postgresql_where=sa.text("valid_until_action_uuid is null"))


def downgrade() -> None:
    op.drop_index("issuer_pubkey_history_one_current_uidx", table_name="issuer_pubkey_history")
    op.drop_index("issuer_pubkey_history_asset_idx", table_name="issuer_pubkey_history")
    op.drop_index("asset_admin_malicious_idx", table_name="asset_admin_annotations")
    op.drop_index("asset_admin_last_action_idx", table_name="asset_admin_annotations")
    op.drop_index("asset_admin_featured_idx", table_name="asset_admin_annotations")
    op.drop_index("asset_admin_delisted_idx", table_name="asset_admin_annotations")
    op.drop_index("asset_admin_asset_type_idx", table_name="asset_admin_annotations")
    op.drop_index("asset_custom_attributes_value_gin_idx", table_name="asset_custom_attributes")
    op.drop_index("asset_custom_attributes_name_idx", table_name="asset_custom_attributes")
    op.drop_index("asset_category_tags_tag_idx", table_name="asset_category_tags")
    op.drop_index("asset_category_tags_asset_position_idx", table_name="asset_category_tags")
    op.drop_index("asset_trading_venues_name_idx", table_name="asset_trading_venues")
    op.drop_index("asset_trading_venues_asset_position_idx", table_name="asset_trading_venues")
    op.drop_index("asset_mutable_metadata_action_idx", table_name="asset_mutable_metadata")
    op.drop_index("actions_received_at_idx", table_name="actions")
    op.drop_index("actions_operation_sequence_idx", table_name="actions")
    op.drop_index("actions_issuer_nonce_uidx", table_name="actions")
    op.drop_index("actions_chain_asset_sequence_idx", table_name="actions")
    op.drop_index("actions_asset_sequence_idx", table_name="actions")
    op.drop_index("actions_actor_sequence_idx", table_name="actions")
    op.drop_index("assets_updated_at_idx", table_name="assets")
    op.drop_index("assets_ticker_idx", table_name="assets")
    op.drop_index("assets_one_active_asset_id_uidx", table_name="assets")
    op.drop_index("assets_name_idx", table_name="assets")
    op.drop_index("assets_domain_ticker_active_uidx", table_name="assets")
    op.drop_index("assets_domain_idx", table_name="assets")
    op.drop_index("assets_created_at_idx", table_name="assets")
    op.drop_index("assets_asset_id_prefix_idx", table_name="assets")
    op.drop_index("assets_asset_id_idx", table_name="assets")
    op.drop_table("issuer_pubkey_history")
    op.drop_table("asset_admin_annotations")
    op.drop_table("asset_custom_attributes")
    op.drop_table("asset_category_tags")
    op.drop_table("asset_trading_venues")
    op.drop_table("asset_mutable_metadata")
    op.drop_table("actions")
    op.drop_table("assets")
