# Retire issuer category tags and trading venues

Date: 2026-11-13

## Implementation Status

Legend: `[x]` complete, `[~]` in progress, `[ ]` not started.

Current progress:

- [x] Confirm scope, audit strategy, compatibility, and rollback behavior.
- [x] Module 1 — Remove API and issuer support.
- [x] Module 2 — Migrate stored values and cached responses.
- [x] Module 3 — Update SDK, specifications, and documentation.
- [~] Module 4 — Validate and prepare rollout (validated locally; production rollout pending).

## Purpose and decisions

Remove `mutable.category_tags` and `mutable.trading_venues` completely. Clear existing values, recording one system action per affected asset. Admin ownership will be implemented separately.

- Remove both normalized storage tables and their ORM models.
- Keep `mutable.custom` and its existing schema version and issuer operations.
- Preserve historical actions, signatures, issuer hash chains, contracts, and legacy contract extras.
- Include active and deregistered asset records, identifying affected records by asset UUID.
- Use a maintenance window with API workers and import jobs stopped.
- A downgrade restores the old schema with empty fields; cleared values remain cleared.

### Implementation notes

- Removed query parameters are rejected by the existing router-level `reject_unknown_query_parameters` dependency (HTTP 400 `validation_error`, `details.parameters`). Dependencies run before the handler calls `prepare_asset_read`, so rejection precedes conditional-read handling.
- Removed registration fields are rejected by `MutableMetadata`'s `extra="forbid"` (HTTP 422 `extra_forbidden`). Removed issuer operations fail the discriminated `IssuerAction` union.
- `Operation.REPLACE_CATEGORY_TAGS` and `Operation.REPLACE_TRADING_VENUES` were removed. Audit storage and filtering use free-form operation strings, so historical entries remain readable.
- `normalize_url`, `require_category_tag`, `require_trading_venue`, and `available_trading_venues` became unused and were removed.
- The system action payload is:
  `{"operation", "asset_id", "migration_revision", "reason", "cleared_fields", "previous_values": {"category_tags": [...], "trading_venues": [{"venue", "url"}]}}`.
  `cleared_fields` always lists both retired fields; `previous_values` records both, empty when unset.
- The migration takes `ACCESS EXCLUSIVE` locks on both retiring tables before reading so no rows can change between recording and dropping.
- Cached v2 timestamps use the same text conversion as the live serializer and are verified byte for byte against it.
- The registry modification time is advanced explicitly, so conditional reads are invalidated even when no rows change.

## Implementation Modules

### Module 1 — API and runtime removal

- [x] Remove both fields from registration models and all current asset response projections, including lookup, search, and cached/uncached exports. `mutable` retains `custom`.
- [x] Remove the `replace_category_tags` and `replace_trading_venues` issuer action variants, handlers, validation, and examples.
- [x] Reject registration payloads containing either removed field, including empty arrays. Removed issuer operations fail existing action validation.
- [x] Remove `category_tag` and `trading_venue` search filters. Explicitly reject requests containing either query parameter with HTTP 400 `validation_error`, before conditional-read handling.
- [x] Remove corresponding relationships, eager loads, insertion logic, and unused validation helpers.
- [x] Preserve arbitrary custom attributes and legacy contract extras, including identically named keys; this change targets the first-class mutable fields.

### Module 2 — Audited database migration

- [x] Add revision `0018_retire_issuer_metadata`, following the current migration head. Freeze migration logic independently of live ORM models and serializers.
- [x] Process assets with rows in either retiring table in UUID order, using batches of 500 within one transaction.
- [x] Insert one unsigned system action per affected asset with operation `retire_issuer_metadata_fields`. Include the asset ID, migration revision, reason, cleared field names, and previous values of both fields in their original order.
- [x] Leave the new action's hash null so it does not advance the issuer hash chain. Use the existing global audit sequence; existing audit endpoints will expose the action.
- [x] Update affected assets' `updated_at` and existing mutable metadata timestamps/action references. Leave unaffected asset timestamps unchanged.
- [x] Remove both keys from every existing v2 cached response, including caches containing empty arrays. Synchronize affected cached asset timestamps with their database values.
- [x] Preserve other cached content and legacy JSON. If a v2 cache cannot be safely decoded, invalidate its `v2_json` and log the asset UUID so live serialization supplies the response.
- [x] Drop both retiring tables after recording their values. Ensure registry modification time advances so conditional reads cannot reuse the previous response representation.
- [x] Log affected asset, removed row, rewritten cache, and invalidated cache counts. Perform no network calls.
- [x] Make downgrade recreate the original tables, indexes, and registry-version triggers, restore empty arrays in cached v2 responses, and preserve migration audit entries.

All changes commit together. A failure rolls back data, audit entries, and schema changes; rerunning the failed upgrade produces no duplicate committed actions.

### Module 3 — SDK, specification, and documentation

- [x] Remove SDK replacement methods, action types, field types, and search options; update examples and fixtures.
- [x] Regenerate `openapi.yaml` and `sdk/src/generated/openapi.d.ts` using the repository's required generation commands.
- [x] Update current API, architecture, schema, and operational documentation. Document the breaking removal, system action, maintenance procedure, and downgrade behavior.

### Module 4 — Validation and rollout

- [x] Test upgrades from the previous head and fresh installation through the full migration chain.
- [x] Cover assets with tags only, venues only, both, or neither; include deregistered records and repeated blockchain IDs across different asset UUIDs.
- [x] Verify exactly one system action per affected UUID, captured previous values, preserved historical actions, unchanged latest issuer action hash, and successful subsequent custom-field actions.
- [x] Verify removed inputs and filters are rejected, and the fields are absent from lookup, search, exports, OpenAPI, and SDK types.
- [x] Cover valid, missing, null, stale, and malformed caches; timestamp parity; conditional-read invalidation; transaction rollback; downgrade and re-upgrade.
- [x] Run the PostgreSQL-backed Python suite, OpenAPI alignment checks, and SDK tests, type checks, lint, formatting checks, and build.
- [ ] Execute the production rollout below.

## Rollout and acceptance

See `docs/deployment.md` for the operator procedure.

1. Take a database backup and record affected-asset counts.
2. Stop API workers and import jobs.
3. Apply the migration and deploy the updated application.
4. Verify migration counts, removed tables, audit visibility, and response shape before reopening traffic.

Success means neither field exists in current first-class asset metadata, issuers cannot repopulate them, and every asset whose stored values were cleared has one explanatory audit entry. Future admin annotation support starts with empty values.
