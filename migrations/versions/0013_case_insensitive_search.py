"""Add indexes for case-insensitive asset prefix searches.

Revision ID: 0013_case_insensitive_search
Revises: 0012_v2_icon_descriptors
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_case_insensitive_search"
down_revision: str | None = "0012_v2_icon_descriptors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "create index assets_ticker_ci_prefix_idx "
            "on assets (lower(ticker) text_pattern_ops)"
        )
    )
    op.execute(
        sa.text(
            "create index assets_name_ci_prefix_idx "
            "on assets (lower(name) text_pattern_ops)"
        )
    )


def downgrade() -> None:
    op.drop_index("assets_name_ci_prefix_idx", table_name="assets")
    op.drop_index("assets_ticker_ci_prefix_idx", table_name="assets")
