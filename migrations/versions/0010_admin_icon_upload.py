"""Allow direct admin icon uploads.

Revision ID: 0010_admin_icon_upload
Revises: 0009_active_asset_icons
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_admin_icon_upload"
down_revision: str | None = "0009_active_asset_icons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "asset_icon_proposals_submission_method_chk",
        "asset_icon_proposals",
        type_="check",
    )
    op.create_check_constraint(
        "asset_icon_proposals_submission_method_chk",
        "asset_icon_proposals",
        "submission_method in ('v2_issuer_signature', 'admin_upload', 'legacy_import')",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            do $$
            begin
                if exists (
                    select 1
                    from asset_icon_proposals
                    where submission_method = 'admin_upload'
                ) then
                    raise exception
                        'cannot downgrade while admin-uploaded icon proposals exist';
                end if;
            end
            $$;
            """
        )
    )
    op.drop_constraint(
        "asset_icon_proposals_submission_method_chk",
        "asset_icon_proposals",
        type_="check",
    )
    op.create_check_constraint(
        "asset_icon_proposals_submission_method_chk",
        "asset_icon_proposals",
        "submission_method in ('v2_issuer_signature', 'legacy_import')",
    )
