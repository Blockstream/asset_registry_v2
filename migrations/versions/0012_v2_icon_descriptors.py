"""Replace cached v2 Base64 icons with content-addressed descriptors.

Revision ID: 0012_v2_icon_descriptors
Revises: 0011_remove_legacy_icons
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_v2_icon_descriptors"
down_revision: str | None = "0011_remove_legacy_icons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ADD_V2_ICON_DESCRIPTORS = sa.text(
    """
    update asset_serialized_fragments as fragments
    set v2_json = jsonb_set(
            fragments.v2_json::jsonb,
            '{icon}',
            case
                when proposals.icon_proposal_uuid is not null
                  and proposals.status = 'approved'
                  and proposals.obsoleted_at is null
                  and proposals.image_data is not null
                then jsonb_build_object(
                    'href',
                    format(
                        '/v2/assets/%s/icon/%s.png',
                        assets.asset_id,
                        proposals.icon_hash
                    )
                )
                else 'null'::jsonb
            end,
            true
        )::text,
        updated_at = now()
    from assets
    left join asset_icon_proposals as proposals
      on proposals.icon_proposal_uuid = assets.active_icon_proposal_uuid
    where fragments.asset_uuid = assets.asset_uuid
      and fragments.v2_json is not null
    """
)

RESTORE_V2_BASE64_ICONS = sa.text(
    """
    update asset_serialized_fragments as fragments
    set v2_json = (
            case
                when proposals.icon_proposal_uuid is not null
                  and proposals.status = 'approved'
                  and proposals.obsoleted_at is null
                  and proposals.image_data is not null
                then jsonb_set(
                    fragments.v2_json::jsonb,
                    '{icon}',
                    to_jsonb(
                        replace(encode(proposals.image_data, 'base64'), E'\n', '')
                    ),
                    true
                )
                else fragments.v2_json::jsonb - 'icon'
            end
        )::text,
        updated_at = now()
    from assets
    left join asset_icon_proposals as proposals
      on proposals.icon_proposal_uuid = assets.active_icon_proposal_uuid
    where fragments.asset_uuid = assets.asset_uuid
      and fragments.v2_json is not null
    """
)


def upgrade() -> None:
    op.execute(ADD_V2_ICON_DESCRIPTORS)


def downgrade() -> None:
    op.execute(RESTORE_V2_BASE64_ICONS)
