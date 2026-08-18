# Explicit Issuer Action Payloads Plan

Date: 2026-05-05

## Implementation Status

Legend:

- `[x]` completed and verified locally where possible
- `[~]` implemented or partially verified, with remaining integration work
- `[ ]` not started

Current progress:

- `[x]` Module 1 - Finalize Action Vocabulary
- `[x]` Module 2 - Schema Refactor
- `[x]` Module 3 - Issuer Action Service Refactor
- `[x]` Module 4 - Tests and No-Op Coverage
- `[x]` Module 5 - OpenAPI and Docs
- `[x]` Module 6 - Compatibility Cleanup

## Purpose

Replace the generic issuer action shape:

```json
{
  "operation": "replace",
  "path": "/mutable/category_tags",
  "value": ["stablecoin"]
}
```

with explicit, operation-specific payloads for known mutable fields:

```json
{
  "operation": "replace_category_tags",
  "category_tags": ["stablecoin"]
}
```

The goal is to make the public API easier to validate, document, audit, sign, and use from clients while preserving targeted updates for custom metadata where they are most useful.

## Design Decision

Do not expose a general JSON Pointer API for first-class registry fields.

Use explicit operation names and explicit property names for controlled mutable fields. Keep custom metadata as the only area with targeted key-level updates, because custom data is open-ended and may become large enough that replacing the whole object for every edit is inconvenient.

## Proposed Issuer Action Vocabulary

First-class mutable metadata:

- `replace_category_tags`
- `replace_trading_venues`

Custom metadata:

- `replace_custom`
- `set_custom_field`
- `delete_custom_field`

Existing issuer lifecycle operations:

- `deregister`
- `rotate_issuer_pubkey`

## Proposed Action Shapes

All actions keep the common signed issuer envelope:

```json
{
  "signing_context": "liquid-asset-registry-action-v1",
  "asset_id": "aa909f1b00000000000000000000000000000000000000000000000000000000",
  "operation": "replace_category_tags",
  "mutable_schema_version": 1,
  "timestamp": "2026-05-05T12:00:00Z",
  "nonce": "9b2b0b3f-8f2e-4f3d-a979-1f2e8a947f87e"
}
```

### `replace_category_tags`

```json
{
  "signing_context": "liquid-asset-registry-action-v1",
  "asset_id": "aa909f1b00000000000000000000000000000000000000000000000000000000",
  "operation": "replace_category_tags",
  "mutable_schema_version": 1,
  "timestamp": "2026-05-05T12:00:00Z",
  "nonce": "9b2b0b3f-8f2e-4f3d-a979-1f2e8a947f87e",
  "category_tags": ["stablecoin", "tokenized"]
}
```

### `replace_trading_venues`

```json
{
  "signing_context": "liquid-asset-registry-action-v1",
  "asset_id": "aa909f1b00000000000000000000000000000000000000000000000000000000",
  "operation": "replace_trading_venues",
  "mutable_schema_version": 1,
  "timestamp": "2026-05-05T12:01:00Z",
  "nonce": "2099dc1c-e751-4b5e-b570-bb71500a36d0",
  "trading_venues": [
    {
      "venue": "sideswap",
      "url": "https://api.sideswap.io/assets/ABT"
    }
  ]
}
```

### `replace_custom`

Replaces the whole custom metadata object.

```json
{
  "signing_context": "liquid-asset-registry-action-v1",
  "asset_id": "aa909f1b00000000000000000000000000000000000000000000000000000000",
  "operation": "replace_custom",
  "mutable_schema_version": 1,
  "timestamp": "2026-05-05T12:02:00Z",
  "nonce": "8a96716d-6018-412c-b38b-105e52f258ea",
  "custom": {
    "isin": "US0000000000",
    "issuer_note": "Series A"
  }
}
```

### `set_custom_field`

Updates or creates one custom metadata key.

```json
{
  "signing_context": "liquid-asset-registry-action-v1",
  "asset_id": "aa909f1b00000000000000000000000000000000000000000000000000000000",
  "operation": "set_custom_field",
  "mutable_schema_version": 1,
  "timestamp": "2026-05-05T12:03:00Z",
  "nonce": "0a6a7177-2f77-40f3-8541-5d44f41933f4",
  "custom_key": "isin",
  "value": "US0000000000"
}
```

### `delete_custom_field`

Deletes one custom metadata key.

```json
{
  "signing_context": "liquid-asset-registry-action-v1",
  "asset_id": "aa909f1b00000000000000000000000000000000000000000000000000000000",
  "operation": "delete_custom_field",
  "mutable_schema_version": 1,
  "timestamp": "2026-05-05T12:04:00Z",
  "nonce": "b4ca4e41-f265-474b-892f-ecb9ff5274c3",
  "custom_key": "isin"
}
```

## Validation Rules

- Remove `path` from public issuer action schemas.
- `replace_category_tags.category_tags` uses the existing controlled category tag validation.
- `replace_trading_venues.trading_venues` uses the existing controlled trading venue and normalized URL validation.
- `replace_custom.custom` must be a JSON object.
- `set_custom_field.custom_key` and `delete_custom_field.custom_key` identify one top-level custom key.
- `custom_key` must not be empty and must not contain `/`.
- `custom_key` should use the same naming validation anywhere custom keys appear.
- `value` for `set_custom_field` accepts JSON values currently accepted for custom metadata.
- The old `replace`/`delete` plus `path` shapes should be removed from the v2 draft unless a temporary compatibility module is explicitly approved.

## No-Op Rules

Keep current no-op semantics, remapped to explicit operations:

- `replace_category_tags` with the current normalized ordered list is rejected.
- `replace_trading_venues` with the current normalized ordered list is rejected.
- `replace_custom` with a JSON-equal custom object is rejected.
- `set_custom_field` with an existing JSON-equal value is rejected.
- `delete_custom_field` for a missing key is rejected.
- `deregister` on an already deregistered asset is rejected.
- `rotate_issuer_pubkey` to the current issuer key is rejected.

Rejected no-ops must not insert an action row, consume a nonce, or consume an audit sequence.

## Audit Behavior

Accepted audit entries should store the exact explicit signed action in `actions.action`.

Historical audit rows using the old shape may exist in non-production/dev data. Readers should treat `action` as raw accepted JSON and not require a single action shape for old rows. New writes should emit only the explicit operation shapes.

## Compatibility Decision

Recommended approach for this initial implementation phase:

- Make this a clean breaking change before external clients integrate.
- Do not accept old `path` actions after the refactor.
- Update tests, OpenAPI, examples, and any helper builders in one change.

If compatibility is later needed, add a short-lived adapter behind an explicit feature flag. The adapter must canonicalize and store the original signed action exactly as submitted, not rewrite audit history into the new shape.

## Implementation Modules

### Module 1 - Finalize Action Vocabulary - Complete

- Confirm operation names:
  - `replace_category_tags`
  - `replace_trading_venues`
  - `replace_custom`
  - `set_custom_field`
  - `delete_custom_field`
  - `deregister`
  - `rotate_issuer_pubkey`
- Confirm that no first-class mutable metadata property names duplicate across nesting levels.
- Confirm custom metadata remains the only area with key-level targeting.

### Module 2 - Schema Refactor - Complete

- Replace `ReplaceAction` with:
  - `ReplaceCategoryTagsAction`
  - `ReplaceTradingVenuesAction`
  - `ReplaceCustomAction`
  - `SetCustomFieldAction`
- Replace `DeleteAction` with `DeleteCustomFieldAction`.
- Keep `DeregisterAction` and `RotateIssuerPubkeyAction`.
- Update the `IssuerAction` union and Pydantic discriminator behavior.
- Remove `path` and `JsonPointerPath` from issuer action schemas.

### Module 3 - Issuer Action Service Refactor - Complete

- Update `submit_issuer_action` dispatch to use explicit action classes.
- Replace path dispatch in `_apply_replace` and `_apply_delete` with operation-specific functions.
- Add whole-object custom replacement behavior.
- Keep custom field set/delete behavior.
- Update no-op checks for the explicit operation classes.
- Keep nonce, freshness, canonical JSON, and signature behavior unchanged.

### Module 4 - Tests and No-Op Coverage - Complete

- Update issuer action tests to sign explicit action payloads.
- Add coverage for `replace_custom`.
- Keep coverage for targeted `set_custom_field` and `delete_custom_field`.
- Keep no-op tests for controlled fields, custom field set/delete, deregister, and key rotation.
- Add schema tests that reject old `path` action shapes.

### Module 5 - OpenAPI and Docs - Complete

- Update `openapi.yaml` issuer action schemas, descriptions, and examples.
- Remove JSON Pointer language from issuer action docs.
- Add `replace_custom`, `set_custom_field`, and `delete_custom_field` examples.
- Update `x-design-notes.supported_mutable_paths` to an operation table or remove it.
- Update `AGENTS.md` if it mentions path-based issuer actions.

### Module 6 - Compatibility Cleanup - Complete

- Search for `/mutable/`, `JsonPointerPath`, `operation: replace`, `operation: delete`, and `path` references.
- Remove obsolete helper constants such as `CUSTOM_PATH_PREFIX` if no longer used.
- Run authored OpenAPI alignment checks.
- Run full local and live PostgreSQL tests.

## Acceptance Criteria

- New issuer mutations do not use `path`.
- OpenAPI exposes only explicit issuer operation payloads.
- Tests prove old path-based issuer action payloads are rejected.
- No-op rejection remains before action insertion.
- Audit rows store the exact explicit signed action payload.
- Full pytest suite passes locally and with `ASSET_REGISTRY_TEST_DATABASE_URL`.

## Implementation Result

- Implemented as a clean breaking change with no old path-based issuer action compatibility adapter.
- Registration and issuer action schemas now reject custom metadata keys containing `/`.
- `replace_custom` replaces the full custom object; `set_custom_field` and `delete_custom_field` target one top-level custom key.
- OpenAPI exposes only explicit issuer action schemas and operation examples.
- Verified with authored OpenAPI parsing/ref checks, focused schema/spec tests, full local pytest, and full live PostgreSQL pytest.

## Open Questions

- Should `replace_custom` require all values to pass the same per-key custom validation as `set_custom_field`?
- Should custom keys be limited to a conservative regex such as `^[A-Za-z0-9_.-]{1,128}$`?
- Should `replace_custom` preserve key ordering in responses through canonical JSON only, or should response ordering remain unspecified?
- Should there be a maximum custom object serialized size before accepting signed writes?
