"""Remove synthesized root null fields from registered contracts.

Revision ID: 0014_preserve_legacy_contract
Revises: 0013_case_insensitive_search
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_preserve_legacy_contract"
down_revision: str | None = "0013_case_insensitive_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The legacy importer used to copy top-level fallback fields into the nested
    # contract whenever the top-level key existed. The historical datasets
    # represent absent contract collection and ticker values as top-level JSON
    # nulls, so the importer synthesized root null fields for those assets.
    #
    # Keep top-level compatibility fields, non-null contract values, and nulls
    # nested inside an actual contract extra value.
    #
    # Imported legacy registration actions did not participate in the action
    # hash chain, and the synthesized nulls make their request contract
    # contradict the contract_hash stored alongside it. Repair those historical
    # payloads before rebuilding any serialized projection from them.
    op.execute(
        """
        update actions
        set action = jsonb_set(
            action,
            '{request,contract}',
            (
                select coalesce(jsonb_object_agg(field.key, field.value), '{}'::jsonb)
                from jsonb_each(action->'request'->'contract') as field(key, value)
                where field.value <> 'null'::jsonb
            )
        )
        where operation = 'legacy_register'
          and action_hash is null
          and jsonb_typeof(action->'request'->'contract') = 'object'
          and exists (
              select 1
              from jsonb_each(action->'request'->'contract') as field(key, value)
              where field.value = 'null'::jsonb
          )
        """
    )
    op.execute(
        """
        update asset_serialized_fragments
        set legacy_json = jsonb_set(
                legacy_json::jsonb,
                '{contract}',
                (
                    select coalesce(jsonb_object_agg(field.key, field.value), '{}'::jsonb)
                    from jsonb_each(legacy_json::jsonb->'contract') as field(key, value)
                    where field.value <> 'null'::jsonb
                )
            )::text
        where exists (
            select 1
            from jsonb_each(legacy_json::jsonb->'contract') as field(key, value)
            where field.value = 'null'::jsonb
        )
        """
    )
    op.execute(
        """
        update asset_serialized_fragments
        set v2_json = jsonb_set(
                v2_json::jsonb,
                '{contract}',
                (
                    select coalesce(jsonb_object_agg(field.key, field.value), '{}'::jsonb)
                    from jsonb_each(v2_json::jsonb->'contract') as field(key, value)
                    where field.value <> 'null'::jsonb
                )
            )::text
        where exists (
            select 1
            from jsonb_each(v2_json::jsonb->'contract') as field(key, value)
            where field.value = 'null'::jsonb
        )
        """
    )
    op.execute(
        """
        update assets
        set contract_extra_fields = (
            select coalesce(jsonb_object_agg(field.key, field.value), '{}'::jsonb)
            from jsonb_each(contract_extra_fields) as field(key, value)
            where field.value <> 'null'::jsonb
        )
        where exists (
            select 1
            from jsonb_each(contract_extra_fields) as field(key, value)
            where field.value = 'null'::jsonb
        )
        """
    )


def downgrade() -> None:
    # The removed key was not part of the registered contract, so restoring it
    # would recreate invalid data.
    pass
