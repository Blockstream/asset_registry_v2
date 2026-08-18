# Core Contract Architecture Plan

Date: 2026-05-22

## Implementation Status

Legend:

- `[x]` completed and verified locally where possible
- `[~]` implemented or partially verified, with remaining integration work
- `[ ]` not started

Current progress:

- `[x]` Module 1 - Contract Reconstruction Boundary
- `[x]` Module 2 - Legacy Contract Extra Field Storage
- `[x]` Module 3 - Response Contract Schema Split
- `[x]` Module 4 - v2-Core Registration Command
- `[x]` Module 5 - Action Writer Boundary
- `[x]` Module 6 - Route Adapter Cleanup
- `[x]` Module 7 - Tests and Compatibility Verification
- `[x]` Module 8 - Documentation

## Purpose

Tighten the architecture now that the v2 registry implementation is mostly complete:

- Make v2 the core internal model while keeping legacy v1 API compatibility at the HTTP edge.
- Centralize contract reconstruction so contract hash behavior is explicit and testable.
- Preserve arbitrary legacy contract fields without making native v2 registrations accept arbitrary fields.
- Move repeated action-row construction into one write boundary.

## Current Observations

Legacy contract arbitrary fields are partially preserved today:

- `LegacyContractMetadata` allows extra fields.
- `legacy_registration_response()` includes `request.contract.model_dump(...)`, which includes accepted extra fields.
- Legacy lookup/listing usually returns the originally stored legacy registration response from the registration action.

But the preservation is not explicit in the asset model:

- `assets` stores only known normalized contract columns.
- Fallback reconstruction in `legacy_response_from_asset()` cannot recover arbitrary legacy contract fields if the original registration action is unavailable.
- v2 `AssetResponse.contract` is reconstructed from columns and cannot currently include arbitrary legacy contract extras.
- Migrated legacy assets can therefore lose hash-relevant legacy contract fields when viewed through v2 projections.

## Design Decisions

### Store only legacy contract extra fields, not the whole contract

Recommended approach:

- Add an `assets.contract_extra_fields` JSONB column.
- Store only unknown legacy contract fields there.
- Keep canonical known fields in first-class columns.
- Keep native v2 registration strict: arbitrary v2 contract fields remain rejected.

Rationale:

- Avoid duplicating data already represented by normalized columns.
- Preserve v1 compatibility and hash-relevant legacy fields.
- Keep search/index fields simple.
- Make the exceptional legacy behavior visible through a specifically named column.

Full contract JSON storage remains a valid fallback if future requirements make contract schemas open-ended across all versions, but it is more redundant than needed for the current v1/v2 split.

### Split request and response contract schemas

Native v2 registration input should continue using strict `ContractMetadata`.

Responses need a contract shape that can represent migrated legacy contracts with extra fields. Add a response-oriented schema, for example:

```python
class ContractMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    entity: ContractEntity
    name: str
    precision: int
    ticker: str | None = None
    version: int
    initial_issuer_pubkey: Pubkey | None = None
    issuer_pubkey: Pubkey | None = None
```

Then `AssetResponse.contract` can include arbitrary legacy contract extras without allowing arbitrary fields in v2 registration requests.

## Compatibility Rules

- Existing v1 registration request behavior remains compatible, except oversized extras remain rejected by the recently added size limits.
- Existing v1 lookup/listing should continue to return original legacy registration shape where available.
- Native v2 registration should remain strict and should not accept arbitrary contract fields.
- v2 lookup for migrated legacy assets should include stored legacy contract extras so the returned contract can reproduce the original legacy contract hash.
- v2 lookup for native v2 assets should continue to return strict v2 contract fields only.
- No `initial_issuer_pubkey` to `issuer_pubkey` conversion should be introduced for native v2 assets.

## Implementation Modules

### Module 1 - Contract Reconstruction Boundary - Completed

- Add a dedicated contract reconstruction helper module, for example `registry_api/contracts.py`.
- Provide helpers such as:
  - `contract_extra_fields_from_legacy_request(request) -> dict`
  - `contract_from_asset(asset) -> dict`
  - `v2_response_contract_from_asset(asset) -> ContractMetadataResponse`
  - `legacy_contract_from_asset(asset) -> dict`
- Ensure known fields and extra fields are merged in a deterministic way.
- Ensure extra fields cannot override known contract fields.
- Use this helper from both v2 response construction and legacy fallback response construction.

### Module 2 - Legacy Contract Extra Field Storage - Completed

- Add `Asset.contract_extra_fields` as a JSONB column with default `{}`.
- In legacy registration, store `request.contract.model_extra or {}`.
- In native v2 registration, store `{}`.
- In migration, preserve the existing stored extras.
- For existing development data, either reset the DB or backfill from the stored legacy registration action where available.
- Add DB constraints or validation so the column always stores an object.

### Module 3 - Response Contract Schema Split - Completed

- Add a response-specific contract schema that allows extra fields.
- Change `AssetResponse.contract` to use the response schema.
- Keep `RegisterAssetRequest.contract` strict.
- Verify generated OpenAPI still clearly communicates that v2 registration request contracts reject extra fields.
- Document that response contracts for migrated legacy assets may include legacy extra fields.

### Module 4 - v2-Core Registration Command - Completed

- Introduce a command object for core registration, for example:

```python
@dataclass(frozen=True)
class RegisterAssetCommand:
    asset_id: str
    contract: dict
    contract_version: int
    domain: str
    name: str
    ticker: str
    precision: int
    domain_verification_method: str
    initial_issuer_pubkey: str
    initial_issuer_pubkey_source: str
    contract_extra_fields: dict
    mutable: MutableMetadata
    source: Literal["legacy", "v2"]
```

- Add adapter functions:
  - `command_from_legacy_registration(request)`
  - `command_from_v2_registration(request)`
- Move common insert behavior into a shared registration service.
- Preserve current public function names initially by having `register_legacy_asset()` and `register_v2_asset()` call the shared service.

### Module 5 - Action Writer Boundary - Completed

- Add an action writer module, for example `registry_api/action_writer.py`.
- Centralize repeated `Action(...)` row creation.
- Include options for:
  - actor
  - operation
  - payload
  - signature
  - nonce
  - verified pubkey
  - issuer/admin timestamp
  - hash-chain participation
- Compute `action_hash` inside the writer when requested.
- Keep state projection logic in service modules; the writer should own audit/action row invariants, not business behavior.

### Module 6 - Route Adapter Cleanup - Completed

- Keep routers focused on HTTP concerns:
  - dependencies
  - headers
  - request body bytes for signed actions
  - response status codes
- Move request-to-command conversion out of routers.
- Keep legacy compatibility logic in legacy adapter/projection modules.
- Keep v2 service logic independent from legacy response shapes.

### Module 7 - Tests and Compatibility Verification - Completed

- Add tests showing legacy contract extras are:
  - accepted when within size limits
  - stored in `assets.contract_extra_fields`
  - returned by legacy lookup/listing
  - returned by v2 lookup after migration
  - included in reconstructed contract hash calculations
- Add tests showing native v2 registration still rejects arbitrary contract fields.
- Add tests proving extra fields cannot override known contract fields.
- Add tests proving current legacy API response shapes are unchanged for standard v1 assets.
- Add tests around action writer hash computation and idempotency behavior.

### Module 8 - Documentation - Completed

- Document that legacy contract extras are preserved for compatibility.
- Document that v2 registration remains strict and should use `mutable.custom` for application-specific data.
- Document that response contracts for migrated legacy assets can include legacy extra fields.
- Document the internal command/action-writer pattern for future maintainers.

## Open Questions

- Should `contract_extra_fields` preserve only legacy contract extras, or also top-level legacy registration extras?
  - Recommended: only contract extras for now, because those are contract-hash-relevant.
- Should v2 lookup for unmigrated legacy assets expose legacy extras?
  - Recommended: yes for legacy endpoints, no v2 actions until migration as today.
- Should fallback legacy responses ever be allowed to reconstruct from columns plus extras if the original registration action exists but is malformed?
  - Recommended: prefer original action response when available; fallback only when it is missing or invalid.
- Should existing development databases be reset or backfilled?
  - Recommended: backfill from legacy registration actions if simple, but a reset is acceptable while still pre-production.
