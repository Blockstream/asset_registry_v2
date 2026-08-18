"""Add cached serialized asset fragments.

Revision ID: 0007_serialized_fragments
Revises: 0006_case_sensitive_ticker
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import select
from sqlalchemy.orm import Session

revision: str = "0007_serialized_fragments"
down_revision: str | None = "0006_case_sensitive_ticker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_serialized_fragments",
        sa.Column("asset_uuid", sa.UUID(), nullable=False),
        sa.Column("legacy_json", sa.Text(), nullable=False),
        sa.Column("v2_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["asset_uuid"], ["assets.asset_uuid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("asset_uuid"),
    )
    op.create_index("asset_serialized_fragments_asset_idx", "asset_serialized_fragments", ["asset_uuid"])
    _backfill_serialized_fragments()


def downgrade() -> None:
    op.drop_index("asset_serialized_fragments_asset_idx", table_name="asset_serialized_fragments")
    op.drop_table("asset_serialized_fragments")


def _backfill_serialized_fragments() -> None:
    from registry_api.models import Asset
    from registry_api.serialized_fragments import refresh_asset_serialized_fragments

    bind = op.get_bind()
    session = Session(bind=bind, autoflush=False, join_transaction_mode="create_savepoint")
    try:
        asset_uuids = list(session.scalars(select(Asset.asset_uuid).order_by(Asset.asset_id.asc())))
        for asset_uuid in asset_uuids:
            asset = session.get(Asset, asset_uuid)
            if asset is not None:
                refresh_asset_serialized_fragments(session, asset)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
