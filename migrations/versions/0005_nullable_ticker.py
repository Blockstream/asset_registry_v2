"""Allow untickered assets to share a domain.

Revision ID: 0005_nullable_ticker
Revises: 0004_contract_extra_fields
Create Date: 2026-06-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_nullable_ticker"
down_revision: str | None = "0004_contract_extra_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("assets_domain_ticker_active_uidx", table_name="assets")
    op.alter_column("assets", "ticker", existing_type=sa.Text(), nullable=True)
    op.execute("update assets set ticker = null where ticker = ''")
    op.create_index(
        "assets_domain_ticker_active_uidx",
        "assets",
        [sa.text("lower(domain)"), sa.text("lower(ticker)")],
        unique=True,
        postgresql_where=sa.text("status = 'active' and ticker is not null and ticker <> ''"),
    )


def downgrade() -> None:
    op.drop_index("assets_domain_ticker_active_uidx", table_name="assets")
    op.execute("update assets set ticker = '' where ticker is null")
    op.alter_column("assets", "ticker", existing_type=sa.Text(), nullable=False)
    op.create_index(
        "assets_domain_ticker_active_uidx",
        "assets",
        [sa.text("lower(domain)"), sa.text("lower(ticker)")],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
