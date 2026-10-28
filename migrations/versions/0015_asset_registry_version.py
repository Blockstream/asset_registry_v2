"""Track a commit-ordered registry modification time for conditional reads.

Revision ID: 0015_asset_registry_version
Revises: 0014_preserve_legacy_contract
Create Date: 2026-10-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_asset_registry_version"
down_revision: str | None = "0014_preserve_legacy_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table that contributes to a legacy or v2 asset response.
ASSET_TABLES = (
    "assets",
    "asset_serialized_fragments",
    "asset_mutable_metadata",
    "asset_trading_venues",
    "asset_category_tags",
    "asset_custom_attributes",
    "asset_admin_annotations",
    "issuer_pubkey_history",
    "actions",
    "asset_icon_proposals",
)


def upgrade() -> None:
    op.execute(
        """
        create table asset_registry_version (
            singleton boolean primary key default true,
            modified_at timestamptz not null,
            constraint asset_registry_version_singleton_chk check (singleton)
        )
        """
    )
    op.execute(
        """
        insert into asset_registry_version (modified_at)
        select date_trunc('second', coalesce(max(updated_at), now()))
        from assets
        """
    )
    # Constraint triggers deferred to commit run after all other work in the
    # transaction, so the row lock below is always the last lock a writer takes
    # and modification times are assigned in commit order. Adding at least a
    # second per commit gives in-flight commits a later date than readers saw.
    op.execute(
        """
        create function bump_asset_registry_version() returns trigger
        language plpgsql as $$
        begin
            if current_setting('asset_registry.version_bumped', true) = 'on' then
                return null;
            end if;
            perform set_config('asset_registry.version_bumped', 'on', true);
            insert into asset_registry_version (modified_at)
            values (date_trunc('second', clock_timestamp()))
            on conflict (singleton) do update set modified_at = greatest(
                date_trunc('second', clock_timestamp()),
                asset_registry_version.modified_at + interval '1 second'
            );
            return null;
        end
        $$
        """
    )
    for table in ASSET_TABLES:
        op.execute(
            f"""
            create constraint trigger {table}_bump_registry_version
            after insert or update or delete on {table}
            deferrable initially deferred
            for each row execute function bump_asset_registry_version()
            """
        )


def downgrade() -> None:
    for table in ASSET_TABLES:
        op.execute(f"drop trigger {table}_bump_registry_version on {table}")
    op.execute("drop function bump_asset_registry_version()")
    op.execute("drop table asset_registry_version")
