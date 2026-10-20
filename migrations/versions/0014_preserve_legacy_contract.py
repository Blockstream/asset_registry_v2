"""Repair provable legacy registration contracts without blocking other rows.

Revision ID: 0014_preserve_legacy_contract
Revises: 0013_case_insensitive_search
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import op

from migrations.legacy_contract_repair import (
    TESTNET_LEGACY_UNCOMPRESSED_ISSUER_PUBKEYS,  # noqa: F401
    repair_legacy_contracts,
)

revision: str = "0014_preserve_legacy_contract"
down_revision: str | None = "0013_case_insensitive_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Revision 0016 repeats this frozen repair for databases that already ran
    # either earlier implementation of 0014.
    repair_legacy_contracts(op.get_bind())


def downgrade() -> None:
    # Restoring synthesized fields would recreate invalid data.
    pass
