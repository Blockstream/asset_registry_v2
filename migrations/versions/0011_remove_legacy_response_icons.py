"""Remove approved icons embedded in cached legacy asset responses.

Revision ID: 0011_remove_legacy_icons
Revises: 0010_admin_icon_upload
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_remove_legacy_icons"
down_revision: str | None = "0010_admin_icon_upload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REMOVE_LEGACY_RESPONSE_ICONS = sa.text(
    """
    update asset_serialized_fragments
    set legacy_json = (legacy_json::jsonb - 'icon')::text,
        updated_at = now()
    where legacy_json::jsonb ? 'icon'
    """
)

RESTORE_LEGACY_RESPONSE_ICONS = sa.text(
    """
    update asset_serialized_fragments as fragments
    set legacy_json = jsonb_set(
            fragments.legacy_json::jsonb,
            '{icon}',
            to_jsonb(replace(encode(proposals.image_data, 'base64'), E'\n', '')),
            true
        )::text,
        updated_at = now()
    from assets
    join asset_icon_proposals as proposals
      on proposals.icon_proposal_uuid = assets.active_icon_proposal_uuid
    where fragments.asset_uuid = assets.asset_uuid
      and proposals.image_data is not null
    """
)


def upgrade() -> None:
    op.execute(REMOVE_LEGACY_RESPONSE_ICONS)


def downgrade() -> None:
    op.execute(RESTORE_LEGACY_RESPONSE_ICONS)
