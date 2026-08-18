"""Add action hashes for issuer action chaining.

Revision ID: 0003_action_hash_chain
Revises: 0002_admin_governance_audit_seq
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_action_hash_chain"
down_revision: str | None = "0002_admin_governance_audit_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("actions", sa.Column("action_hash", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "actions_action_hash_format_chk",
        "actions",
        "action_hash is null or action_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_index(
        "actions_asset_hash_sequence_idx",
        "actions",
        ["asset_uuid", "audit_sequence"],
        postgresql_where=sa.text("action_hash is not null"),
    )


def downgrade() -> None:
    op.drop_index("actions_asset_hash_sequence_idx", table_name="actions")
    op.drop_constraint("actions_action_hash_format_chk", "actions", type_="check")
    op.drop_column("actions", "action_hash")
