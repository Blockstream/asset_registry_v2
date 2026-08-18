# Issuer Action Hash Chain Plan

Date: 2026-05-13

## Implementation Status

Legend:

- `[x]` completed and verified locally where possible
- `[~]` implemented or partially verified, with remaining integration work
- `[ ]` not started

Current progress:

- `[x]` Module 1 - Finalize Hash Chain Semantics
- `[~]` Module 2 - Development Database Reset
- `[x]` Module 3 - Database Schema
- `[x]` Module 4 - Action Hash Computation
- `[x]` Module 5 - Registration and Migration Anchors
- `[x]` Module 6 - Issuer Action Validation
- `[x]` Module 7 - Latest Action Hash Endpoint
- `[x]` Module 8 - Response and Error Shapes
- `[x]` Module 9 - Tests
- `[x]` Module 10 - OpenAPI and Documentation
- `[x]` Module 11 - SDK Follow-Up

## Purpose

Add an issuer-verifiable hash chain for asset-scoped issuer actions.

Today, publicly auditable ordering is mostly enforced by issuer-signed timestamps plus server audit sequencing. That leaves a gap: if the registry server accepts an issuer action and later omits it from returned history, the issuer may not be able to detect the missing action from the next action alone.

The proposed chain requires each issuer action to include the hash of the latest accepted chain action for that asset:

```json
{
  "operation": "replace_custom",
  "prev_action_hash": "b4b6f2..."
}
```

The registry rejects the action when `prev_action_hash` does not match the latest accepted action hash. This gives issuers and auditors a simple continuity check for issuer-controlled asset history.

## Design Decision

Use `prev_action_hash` in signed issuer action payloads and `action_hash` in registry responses.

`action_hash` is the SHA-256 hex digest of the canonical JSON bytes of the action object that participates in the hash chain. The hash input must be exactly reproducible from the stored `actions.action` JSON object using the registry canonical JSON rules.

Do not include admin actions in the issuer hash chain for this phase.

Rationale:

- Admin actions affect admin/moderation state, not issuer-controlled mutable metadata.
- Requiring issuers to acknowledge admin actions would force clients to fetch the latest hash before every issuer action whenever an admin changes unrelated moderation fields.
- Admin action continuity can be added later as a separate admin or global audit hash chain.

## Development Data Reset Decision

This service is still in development, so this plan intentionally avoids a complex production backfill migration.

Before or as part of implementation, delete current development database data and rebuild from migrations/fixtures. Existing action rows do not need to be backfilled.

Recommended local reset options:

- If using Docker volumes: `docker compose down -v && docker compose up -d`
- If preserving containers: truncate registry tables in dependency order, then run tests/fixtures again
- If migrations are recreated or amended before production: rebuild the database from scratch

The implementation may add a migration for the new column, but it does not need a backfill path for existing rows.

## Hash Chain Scope

Participating rows:

- v2 registration action (`operation = register`)
- legacy-to-v2 migration action (`operation = migrate_contract_metadata`) when it is the chain anchor for a migrated asset
- issuer actions submitted to `POST /v2/assets/{asset_id}/actions`

Non-participating rows:

- legacy v1 registration/deregistration actions that have not been migrated into the v2 model
- admin lifecycle actions
- admin asset-scoped actions
- admin annotation updates
- rejected actions, including validation failures and no-op rejections

## Hash Input

For every participating action row:

1. Take the action object stored in `actions.action`.
2. Serialize with canonical JSON:
   - UTF-8
   - lexicographically sorted object keys
   - no insignificant whitespace
   - deterministic scalar/array/object encoding
3. Compute SHA-256 over those canonical bytes.
4. Store lowercase hex in `actions.action_hash`.

The hash does not include:

- database UUIDs
- audit sequence
- server timestamps
- HTTP headers
- signatures
- mutable/admin projected state

This keeps the hash independently reproducible from the signed/stored action payload.

## First Action Rules

For v2 assets:

- The registration action is the first chain anchor.
- The first issuer action must set `prev_action_hash` to the registration action hash.

For migrated legacy assets:

- The migration action is the first chain anchor.
- The first issuer action after migration must set `prev_action_hash` to the migration action hash.

For unmigrated legacy assets:

- v2 issuer actions remain rejected.
- No hash chain is required until migration.

## Error Shape

When `prev_action_hash` is missing, malformed, or stale, return a registry validation error that includes the expected hash.

Recommended error code:

```json
{
  "error": "prev_action_hash_mismatch",
  "message": "prev_action_hash does not match latest accepted action",
  "details": {
    "asset_id": "aa909f1b00000000000000000000000000000000000000000000000000000000",
    "expected_prev_action_hash": "b4b6f2...",
    "submitted_prev_action_hash": "deadbeef..."
  }
}
```

If no participating action exists for an asset, return a server-side consistency error rather than accepting a null chain. Under the development reset decision, every v2-capable asset should have a registration or migration anchor.

## Latest Action Hash Endpoint

Add an explicit endpoint for clients to fetch the latest action hash before signing the next issuer action:

```http
GET /v2/assets/{asset_id}/actions/latest
```

Response:

```json
{
  "asset_id": "aa909f1b00000000000000000000000000000000000000000000000000000000",
  "action_hash": "b4b6f2...",
  "audit_id": 123,
  "operation": "replace_custom"
}
```

Rules:

- Return the latest participating action for the active asset.
- Exclude admin actions.
- Exclude unmigrated legacy assets from v2 action hash lookup unless a deliberate legacy behavior is added.
- Return `asset_not_found` for missing, deregistered, or v2-ineligible assets, consistent with other active-asset v2 endpoints.

## Implementation Modules

### Module 1 - Finalize Hash Chain Semantics - Complete

- Confirm field name: `prev_action_hash`.
- Confirm response field name: `action_hash`.
- Confirm hash algorithm: SHA-256 over canonical JSON bytes.
- Confirm hex encoding: lowercase 64-character hex.
- Confirm admin actions are excluded from the issuer chain.
- Add `prev_action_hash_mismatch` to central error codes.

### Module 2 - Development Database Reset - Partial

- `[x]` Decide the exact local reset approach for current development data.
- `[ ]` Run the destructive reset against any shared/current development database before relying on existing rows.
- `[x]` Document that no production backfill is required for this phase.
- `[x]` Ensure tests start from clean schema/data.

Note: implementation keeps `actions.action_hash` nullable so old rows do not break schema upgrade, but existing pre-chain assets will not have an issuer-chain anchor until the development database is reset or the asset is re-created/migrated.

### Module 3 - Database Schema - Complete

- `[x]` Add `action_hash` to the `actions` table.
- `[~]` Make the column non-null for chain-participating rows in application logic; the DB column remains nullable for admin/legacy rows and development upgrade tolerance.
- `[x]` Add an index for latest action hash lookup by `asset_uuid` and audit ordering.
- `[x]` Consider a partial unique index on `action_hash` for participating rows if useful; skipped for now because hash collisions are not an operational uniqueness mechanism.
- `[x]` Keep admin lifecycle table unchanged for this phase.

### Module 4 - Action Hash Computation - Complete

- `[x]` Add a helper such as `action_hash(action: dict[str, Any]) -> str`.
- `[x]` Use the same canonical JSON implementation as signed actions.
- `[x]` Add unit tests proving stable hashes for key-order variants.
- `[x]` Ensure hash computation happens before or during action row insertion.

### Module 5 - Registration and Migration Anchors - Complete

- `[x]` Compute and store `action_hash` for v2 registration actions.
- `[x]` Compute and store `action_hash` for v1-to-v2 migration actions.
- `[x]` Return the registration action hash through the latest-action endpoint instead of expanding registration responses.
- `[x]` Ensure migrated legacy assets use the migration action hash as their first expected `prev_action_hash`.

### Module 6 - Issuer Action Validation - Complete

- `[x]` Add `prev_action_hash` to the base issuer action schema.
- `[x]` Validate it as 64-character lowercase/normalized hex.
- `[x]` Before signature verification or action application, load the latest participating action hash for the asset.
- `[x]` Reject when submitted `prev_action_hash` does not match the latest hash.
- `[x]` Include `expected_prev_action_hash` in the error details.
- `[x]` Preserve existing nonce idempotency behavior:
  - exact same canonical signed action and nonce remains idempotent
  - different action with same nonce remains `nonce_conflict`
- `[x]` Preserve no-op rejection before action insertion.

### Module 7 - Latest Action Hash Endpoint - Complete

- `[x]` Add service function for latest participating action lookup.
- `[x]` Add `GET /v2/assets/{asset_id}/actions/latest`.
- `[x]` Add response schema with `asset_id`, `action_hash`, `audit_id`, `operation`, and `server_received_at`.
- `[x]` Validate asset ID using existing normalization.
- `[x]` Exclude admin actions.

### Module 8 - Response and Error Shapes - Complete

- `[x]` Add `action_hash` to accepted issuer action responses.
- `[x]` Include `prev_action_hash` in audit response projections as part of the exact signed action payload.
- `[x]` Add `prev_action_hash_mismatch` to `ErrorCode`.
- `[x]` Keep error responses JSON-compatible and stable.

### Module 9 - Tests - Complete

- `[x]` Registration stores an action hash.
- `[x]` Migration stores an action hash.
- `[x]` Latest action hash endpoint returns the registration/migration hash before any issuer actions.
- `[x]` Issuer action with correct `prev_action_hash` is accepted and advances latest hash.
- `[x]` Issuer action with stale/missing/wrong `prev_action_hash` is rejected with expected hash in details.
- `[x]` Idempotent retry with same nonce and same action remains idempotent.
- `[x]` Admin actions do not change latest issuer action hash.
- `[x]` Unmigrated legacy assets cannot use the v2 issuer action hash chain.

### Module 10 - OpenAPI and Documentation - Complete

- `[x]` Add `prev_action_hash` to issuer action schemas.
- `[x]` Add `action_hash` to relevant response schemas.
- `[x]` Add latest action hash endpoint to the OpenAPI contract.
- `[x]` Document hash input precisely.
- `[x]` Document that admin actions are excluded from the issuer hash chain.
- `[x]` Document the development database reset decision.

### Module 11 - SDK Follow-Up - Complete

- `[x]` Add `getLatestActionHash(assetId)` helper.
- `[x]` Include `prev_action_hash` in issuer action builders.
- `[x]` Update examples to fetch latest hash before signing.
- `[x]` Surface `expected_prev_action_hash` from mismatch errors through existing HTTP error response details.

## Open Questions

- Resolved: registration does not expand `POST /v2/assets`; clients use `GET /v2/assets/{asset_id}/actions/latest`.
- Resolved: the latest hash endpoint includes `server_received_at` for operator debugging.
- Resolved: hash-chain validation happens after schema validation, asset lookup, and nonce idempotency checks, but before timestamp freshness and signature verification.
- Should future admin/global hash chains use the same `action_hash` column or a separate table/column? Recommended: reuse `action_hash`, but keep chain lookup scopes separate.
