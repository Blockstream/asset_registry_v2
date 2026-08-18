"""Make the active asset icon explicit and preserve proposal history.

Revision ID: 0009_active_asset_icons
Revises: 0008_asset_icon_proposals
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_active_asset_icons"
down_revision: str | None = "0008_asset_icon_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "asset_icon_proposals",
        sa.Column("obsoleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "asset_icon_proposals",
        sa.Column("obsoleted_by_action_uuid", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "asset_icon_proposals_obsoleted_action_fk",
        "asset_icon_proposals",
        "actions",
        ["obsoleted_by_action_uuid"],
        ["action_uuid"],
    )

    op.drop_index(
        "asset_icon_proposals_one_pending_uidx",
        table_name="asset_icon_proposals",
    )
    op.create_index(
        "asset_icon_proposals_one_pending_uidx",
        "asset_icon_proposals",
        ["asset_uuid"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' and obsoleted_at is null"),
    )
    op.drop_index(
        "asset_icon_proposals_one_current_uidx",
        table_name="asset_icon_proposals",
    )

    op.add_column(
        "assets",
        sa.Column("active_icon_proposal_uuid", sa.UUID(), nullable=True),
    )
    op.execute(
        """
        update assets
        set active_icon_proposal_uuid = proposals.icon_proposal_uuid
        from asset_icon_proposals as proposals
        where proposals.asset_uuid = assets.asset_uuid
          and proposals.status = 'approved'
          and proposals.image_data is not null
        """
    )
    op.create_foreign_key(
        "assets_active_icon_proposal_fk",
        "assets",
        "asset_icon_proposals",
        ["active_icon_proposal_uuid"],
        ["icon_proposal_uuid"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "assets_active_icon_proposal_fk",
        "assets",
        type_="foreignkey",
    )
    op.execute(
        """
        update asset_icon_proposals as proposals
        set image_data = null
        from assets
        where proposals.asset_uuid = assets.asset_uuid
          and proposals.status = 'approved'
          and proposals.icon_proposal_uuid
              is distinct from assets.active_icon_proposal_uuid
        """
    )
    op.drop_column("assets", "active_icon_proposal_uuid")

    op.create_index(
        "asset_icon_proposals_one_current_uidx",
        "asset_icon_proposals",
        ["asset_uuid"],
        unique=True,
        postgresql_where=sa.text("status = 'approved' and image_data is not null"),
    )
    op.drop_index(
        "asset_icon_proposals_one_pending_uidx",
        table_name="asset_icon_proposals",
    )
    op.create_index(
        "asset_icon_proposals_one_pending_uidx",
        "asset_icon_proposals",
        ["asset_uuid"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_constraint(
        "asset_icon_proposals_obsoleted_action_fk",
        "asset_icon_proposals",
        type_="foreignkey",
    )
    op.drop_column("asset_icon_proposals", "obsoleted_by_action_uuid")
    op.drop_column("asset_icon_proposals", "obsoleted_at")
