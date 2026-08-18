"""Add asset icon proposal and review storage.

Revision ID: 0008_asset_icon_proposals
Revises: 0007_serialized_fragments
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_asset_icon_proposals"
down_revision: str | None = "0007_serialized_fragments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_icon_proposals",
        sa.Column(
            "icon_proposal_uuid",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("asset_uuid", sa.UUID(), nullable=False),
        sa.Column("icon_hash", sa.String(length=64), nullable=False),
        sa.Column("image_data", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("submission_method", sa.Text(), nullable=False),
        sa.Column("proposed_by_action_uuid", sa.UUID(), nullable=False),
        sa.Column("decided_by_action_uuid", sa.UUID(), nullable=True),
        sa.Column(
            "proposed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "icon_hash ~ '^[0-9a-f]{64}$'", name="asset_icon_proposals_hash_format_chk"
        ),
        sa.CheckConstraint(
            "status in ('pending', 'rejected', 'approved')",
            name="asset_icon_proposals_status_chk",
        ),
        sa.CheckConstraint(
            "submission_method in ('v2_issuer_signature', 'legacy_import')",
            name="asset_icon_proposals_submission_method_chk",
        ),
        sa.CheckConstraint(
            "status <> 'pending' or image_data is not null",
            name="asset_icon_proposals_pending_data_chk",
        ),
        sa.CheckConstraint(
            "status <> 'rejected' or image_data is null",
            name="asset_icon_proposals_rejected_data_chk",
        ),
        sa.CheckConstraint(
            "image_data is null or octet_length(image_data) <= 1048576",
            name="asset_icon_proposals_data_size_chk",
        ),
        sa.ForeignKeyConstraint(
            ["asset_uuid"], ["assets.asset_uuid"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["proposed_by_action_uuid"], ["actions.action_uuid"]),
        sa.ForeignKeyConstraint(["decided_by_action_uuid"], ["actions.action_uuid"]),
        sa.PrimaryKeyConstraint("icon_proposal_uuid"),
    )
    op.create_index(
        "asset_icon_proposals_asset_idx",
        "asset_icon_proposals",
        ["asset_uuid", "proposed_at"],
    )
    op.create_index(
        "asset_icon_proposals_status_date_idx",
        "asset_icon_proposals",
        ["status", "proposed_at", "icon_proposal_uuid"],
    )
    op.create_index(
        "asset_icon_proposals_one_pending_uidx",
        "asset_icon_proposals",
        ["asset_uuid"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "asset_icon_proposals_one_current_uidx",
        "asset_icon_proposals",
        ["asset_uuid"],
        unique=True,
        postgresql_where=sa.text("status = 'approved' and image_data is not null"),
    )


def downgrade() -> None:
    op.drop_index(
        "asset_icon_proposals_one_current_uidx", table_name="asset_icon_proposals"
    )
    op.drop_index(
        "asset_icon_proposals_one_pending_uidx", table_name="asset_icon_proposals"
    )
    op.drop_index(
        "asset_icon_proposals_status_date_idx", table_name="asset_icon_proposals"
    )
    op.drop_index("asset_icon_proposals_asset_idx", table_name="asset_icon_proposals")
    op.drop_table("asset_icon_proposals")
