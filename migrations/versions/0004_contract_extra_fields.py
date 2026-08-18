"""Store legacy contract extra fields.

Revision ID: 0004_contract_extra_fields
Revises: 0003_action_hash_chain
Create Date: 2026-05-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_contract_extra_fields"
down_revision: str | None = "0003_action_hash_chain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column(
            "contract_extra_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "assets_contract_extra_fields_object_chk",
        "assets",
        "jsonb_typeof(contract_extra_fields) = 'object'",
    )
    op.execute(
        """
        update assets as asset
        set contract_extra_fields = coalesce(extra_fields.extra_fields, '{}'::jsonb)
        from (
            select distinct on (action.asset_uuid)
                action.asset_uuid,
                (
                    select coalesce(jsonb_object_agg(key, value), '{}'::jsonb)
                    from jsonb_each(action.action->'request'->'contract') as field(key, value)
                    where key not in ('entity', 'issuer_pubkey', 'name', 'ticker', 'precision', 'version')
                ) as extra_fields
            from actions as action
            where action.operation = 'legacy_register'
              and jsonb_typeof(action.action->'request'->'contract') = 'object'
            order by action.asset_uuid, action.audit_sequence asc
        ) as extra_fields
        where asset.asset_uuid = extra_fields.asset_uuid
        """
    )


def downgrade() -> None:
    op.drop_constraint("assets_contract_extra_fields_object_chk", "assets", type_="check")
    op.drop_column("assets", "contract_extra_fields")
