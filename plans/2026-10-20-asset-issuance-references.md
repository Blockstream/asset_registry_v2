# Persist Asset Issuance References

Date: 2026-10-20 (Consolidated: 2026-10-27)

## Implementation Status

- `[x]` Complete
- `[~]` In progress
- `[ ]` Not started

Current progress:

- [x] Review issue #10 and registration, import, and serialization paths.
- [x] Module 1 — Add schema migration, frozen contract repairs, and local backfill.
- [x] Module 2 — Persist references during registration and import with single-pass parsing.
- [x] Module 3 — Update responses, SDK types, documentation, and explicit maintenance backfill.
- [x] Module 4 — Validate migrations, timestamp parity, and backward compatibility.
- [x] Module 5 — Review hardening: forward migration 0017, compact fragments, and efficiency improvements.

## Purpose

Fix [issue #10](https://github.com/Blockstream/asset_registry_v2/issues/10) by persisting and returning original issuance transaction inputs and spent outpoints for legacy and v2 assets.

## Design Decisions

- Store four nullable columns: `issuance_txid`, `issuance_vin`, `issuance_prevout_txid`, and `issuance_prevout_vout`. Each reference must be complete or absent, enforced by explicit PostgreSQL check constraints (`((a IS NULL AND b IS NULL) OR (a IS NOT NULL AND b IS NOT NULL AND valid a AND valid b))`; the `IS NOT NULL` guards keep a half-set pair from evaluating to NULL, which a CHECK would accept).
- Backfill each reference from the top-level response saved in the earliest legacy registration action, falling back to cached legacy JSON. Do not source references from contract extras. Validate values and report missing, malformed, and conflicting data counts.
- Preserve contracts, contract extras, historical actions, and asset timestamps; contract fields may affect the contract hash.
- Perform no network calls during migration or verification-disabled registration. Unavailable references remain nullable until explicitly backfilled.
- Verified chain metadata takes precedence over explicitly supplied trusted-import metadata. Ordinary client extras do not populate these columns.
- Expose `{txid, vin}` and `{txid, vout}` objects through legacy and v2 responses. Legacy omits unknown values; v2 emits nulls. Build response dictionaries directly from database-constrained columns (`issuance_payload_from_asset`) to avoid repeated Pydantic model construction overhead during list/export queries.
- Retain raw saved v1 historical fields when typed references are unknown; known, validated references take precedence.
- Accept external extension keys in issuance references without error (`extra="ignore"`), while strictly validating required txid and index values.
- Apply migrations offline: provide an explicit, retryable chain maintenance command (`registry_api.issuance_backfill`) for assets whose references are missing from local history, with connection pooling, dry-run, row-level locking, and conflict detection.

### Review Resolutions & Architectural Decisions

| Candidate / Issue | Resolution |
| --- | --- |
| 1: Already-applied 0014 is skipped | New revision `0017_reconcile_legacy_data` repeats provable repairs for existing databases without requiring migration rollbacks. |
| 2: Cached timestamp spelling differs | `0017` normalizes v2 asset and admin-action timestamps to ISO 8601 UTC with `Z` suffix. Historical `0007` is frozen in pure SQL independent of live serializers. |
| 3: One bad legacy row blocks upgrades | Report and skip unprovable rows; preserve hash-chained actions (`if action_hash is not None: raise ValueError`). |
| 4: External extension keys rejected | `IssuanceReference` sets `extra="ignore"` to safely accept forward-compatible fields from Esplora or listing dumps while strictly validating txid/vin/vout. |
| 5: Old v2 references absent locally | Keep migrations offline; provide a retryable, explicit maintenance command (`registry_api.issuance_backfill`) with `--dry-run` and `--limit` options. |
| 6: Fragment with only v2_json | Not reachable under required `legacy_json NOT NULL` schema; missing fragment rows use live serializers on demand. |
| 7: Migration depends on live code | Freeze canonical encoding, wallycore hashing/asset derivation, and contract repair in isolated `migrations/legacy_contract_repair.py`. |
| 8: Legacy fields dropped if typed validation fails | Retain raw saved v1 fields in `legacy_response` when columns are unknown. `0017` restores recoverable fields removed by earlier migration passes. |
| 9: Repeated response validation | Direct projection via `issuance_payload_from_asset(asset)` avoids constructing Pydantic models for validated database rows. |
| 10: Duplicate import parsing | Internal `_parse_listing_item` parses issuance metadata once, preserving the public 2-tuple helper signature for callers. |
| 11: Efficiency of Esplora chain verifier | `EsploraChainVerifier` pools its `httpx.Client` connection across queries (reusing TCP/TLS sessions) and supports context manager cleanup and explicit `.close()`. |
| 12: Check constraint boolean clarity | Explicitly format constraints as `((txid IS NULL AND vin IS NULL) OR (txid IS NOT NULL AND vin IS NOT NULL AND ...))` rather than relying on SQL three-valued logic. |

## Implementation Modules

### Module 1 — Schema migration, frozen repairs, and forward reconciliation

- [x] Add nullable columns and explicit paired, hash-format, and unsigned-index constraints.
- [x] Backfill all asset statuses by UUID in bounded batches, preserving source data and timestamps.
- [x] Update existing legacy/v2 fragments and support downgrade without erasing legacy references.
- [x] Freeze revision `0007` projection logic in pure SQL so historical upgrades do not depend on future models.
- [x] Share frozen, bounded contract repair (`migrations/legacy_contract_repair.py`) between `0014` and forward revision `0017`.
- [x] Restore recoverable contract nulls and original uncompressed issuer keys.
- [x] Skip unprovable rows and preserve cryptographic audit hash chains.
- [x] Write compact UTF-8 fragment JSON and skip unchanged fragment updates in `0017`.

### Module 2 — Registration, import, and runtime compatibility

- [x] Return typed issuance metadata from successful Esplora verification using `wallycore` primitives.
- [x] Accept external extension fields without emitting them in typed responses (`extra="ignore"`).
- [x] Persist verified references with legacy/v2 registration and explicitly validated legacy imports.
- [x] Strip untrusted user-supplied issuance extras during registration.
- [x] Parse issuance input once per import item in `_parse_listing_item`, including dry runs.
- [x] Build response dictionaries directly from constrained columns (`issuance_payload_from_asset`).
- [x] Retain raw legacy history where typed references are unknown.

### Module 3 — API, SDK, tooling, and documentation

- [x] Update registration, lookup, search, and cached/uncached export serializers.
- [x] Add typed v2 response properties (`issuance_txin`, `issuance_prevout`) and SDK legacy response fields.
- [x] Regenerate and verify OpenAPI (`openapi.yaml`) and SDK snapshots (`sdk/src/generated/openapi.d.ts`, `sdk/src/types/index.ts`).
- [x] Add batched, retryable missing-reference maintenance CLI tool (`registry_api/issuance_backfill.py`).
- [x] Implement HTTP client pooling and context management in `EsploraChainVerifier` to eliminate repeated TLS handshakes.
- [x] Document storage, source precedence, missing-reference behavior, and backfill CLI in `ARCHITECTURE.md`, `README.md`, and `schema.md`.

### Module 4 — Validation and compatibility

- [x] Test fresh installation, populated historical upgrades (0007, 0014, 0016, 0017), current-head upgrade, and downgrade.
- [x] Test malformed values, precedence, partial/missing data, constraints, batching, and repeated registrations.
- [x] Test compact JSON formatting and unchanged-row skipping during fragment reconciliation.
- [x] Test Esplora error handling, non-object JSON responses, and HTTP connection pooling.
- [x] Check registration/import persistence, migration preservation, and cached/uncached response parity.
- [x] Run the Python test suite, OpenAPI alignment, and TypeScript SDK checks.

## Validation Results

- Full test suite passed across all non-skipped tests; migration regression tests verify frozen contract repairs across historical testnet and production datasets.
- 118 SDK tests, generated-type checks, lint, formatting, and package build pass.
- Ruff and mypy checks pass with zero new diagnostics introduced.
