"""Add signed admin governance tables and global audit sequence.

Revision ID: 0002_admin_governance_audit_seq
Revises: 0001_initial_schema
Create Date: 2026-04-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_admin_governance_audit_seq"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("create sequence if not exists audit_sequence_global")
    op.execute(
        """
        select setval(
            'audit_sequence_global',
            greatest(coalesce((select max(audit_sequence) from actions), 0) + 1, 1),
            false
        )
        """
    )
    op.execute("alter table actions alter column audit_sequence drop identity if exists")
    op.alter_column(
        "actions",
        "audit_sequence",
        server_default=sa.text("nextval('audit_sequence_global')"),
        existing_type=sa.BigInteger(),
        existing_nullable=False,
    )

    op.create_table(
        "admin_keys",
        sa.Column("admin_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("pubkey", sa.String(length=66), nullable=False),
        sa.Column("friendly_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("created_by_admin_action_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("removed_by_admin_action_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("pubkey ~ '^(02|03)[0-9a-f]{64}$'", name="admin_keys_pubkey_format_chk"),
        sa.CheckConstraint("status in ('active', 'removed')", name="admin_keys_status_chk"),
        sa.PrimaryKeyConstraint("admin_uuid"),
        sa.UniqueConstraint("pubkey", name="admin_keys_pubkey_uidx"),
    )

    op.create_table(
        "admin_permissions",
        sa.Column("admin_permission_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("admin_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["admin_uuid"], ["admin_keys.admin_uuid"]),
        sa.PrimaryKeyConstraint("admin_permission_uuid"),
        sa.UniqueConstraint("admin_uuid", "permission", name="admin_permissions_admin_permission_uidx"),
    )

    op.create_table(
        "admin_actions",
        sa.Column("admin_action_uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("audit_sequence", sa.BigInteger(), server_default=sa.text("nextval('audit_sequence_global')"), nullable=False),
        sa.Column("actor_admin_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_pubkey", sa.String(length=66), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("action", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("admin_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("actor_pubkey ~ '^(02|03)[0-9a-f]{64}$'", name="admin_actions_actor_pubkey_format_chk"),
        sa.ForeignKeyConstraint(["actor_admin_uuid"], ["admin_keys.admin_uuid"]),
        sa.PrimaryKeyConstraint("admin_action_uuid"),
        sa.UniqueConstraint("audit_sequence", name="admin_actions_audit_sequence_uidx"),
    )

    op.create_foreign_key(
        "admin_keys_created_action_fkey",
        "admin_keys",
        "admin_actions",
        ["created_by_admin_action_uuid"],
        ["admin_action_uuid"],
    )
    op.create_foreign_key(
        "admin_keys_removed_action_fkey",
        "admin_keys",
        "admin_actions",
        ["removed_by_admin_action_uuid"],
        ["admin_action_uuid"],
    )

    op.create_index("admin_keys_status_idx", "admin_keys", ["status"])
    op.create_index("admin_permissions_permission_idx", "admin_permissions", ["permission"])
    op.create_index("admin_actions_sequence_idx", "admin_actions", ["audit_sequence"])
    op.create_index("admin_actions_actor_nonce_uidx", "admin_actions", ["actor_admin_uuid", "nonce"], unique=True)
    op.create_index("admin_actions_operation_sequence_idx", "admin_actions", ["operation", "audit_sequence"])
    op.create_index("admin_actions_received_at_idx", "admin_actions", ["server_received_at", "audit_sequence"])


def downgrade() -> None:
    op.drop_index("admin_actions_received_at_idx", table_name="admin_actions")
    op.drop_index("admin_actions_operation_sequence_idx", table_name="admin_actions")
    op.drop_index("admin_actions_actor_nonce_uidx", table_name="admin_actions")
    op.drop_index("admin_actions_sequence_idx", table_name="admin_actions")
    op.drop_index("admin_permissions_permission_idx", table_name="admin_permissions")
    op.drop_index("admin_keys_status_idx", table_name="admin_keys")
    op.drop_constraint("admin_keys_removed_action_fkey", "admin_keys", type_="foreignkey")
    op.drop_constraint("admin_keys_created_action_fkey", "admin_keys", type_="foreignkey")
    op.drop_table("admin_actions")
    op.drop_table("admin_permissions")
    op.drop_table("admin_keys")
    op.alter_column("actions", "audit_sequence", server_default=None, existing_type=sa.BigInteger(), existing_nullable=False)
    op.execute("alter table actions alter column audit_sequence add generated by default as identity")
    op.execute("drop sequence if exists audit_sequence_global")
