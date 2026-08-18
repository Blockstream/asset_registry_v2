"""Make ticker namespace uniqueness case-sensitive.

Revision ID: 0006_case_sensitive_ticker
Revises: 0005_nullable_ticker
Create Date: 2026-06-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_case_sensitive_ticker"
down_revision: str | None = "0005_nullable_ticker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("assets_domain_ticker_active_uidx", table_name="assets")
    op.create_index(
        "assets_domain_ticker_active_uidx",
        "assets",
        [sa.text("lower(domain)"), "ticker"],
        unique=True,
        postgresql_where=sa.text("status = 'active' and ticker is not null and ticker <> ''"),
    )


def downgrade() -> None:
    op.drop_index("assets_domain_ticker_active_uidx", table_name="assets")
    op.create_index(
        "assets_domain_ticker_active_uidx",
        "assets",
        [sa.text("lower(domain)"), sa.text("lower(ticker)")],
        unique=True,
        postgresql_where=sa.text("status = 'active' and ticker is not null and ticker <> ''"),
    )
