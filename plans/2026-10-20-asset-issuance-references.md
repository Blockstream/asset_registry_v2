# Persist Asset Issuance References

Date: 2026-10-20

## Implementation Status

- `[x]` Complete
- `[~]` In progress
- `[ ]` Not started

Current progress:

- [x] Review issue #10 and registration, import, and serialization paths.
- [x] Module 1 — Add schema migration and local backfill.
- [x] Module 2 — Persist references during registration and import.
- [x] Module 3 — Update responses, SDK types, and documentation.
- [x] Module 4 — Validate migrations and compatibility.

## Purpose

Fix [issue #10](https://github.com/Blockstream/asset_registry_v2/issues/10) by persisting and returning original issuance transaction inputs and spent outpoints for legacy and v2 assets.

## Design Decisions

- Store four nullable columns: `issuance_txid`, `issuance_vin`, `issuance_prevout_txid`, and `issuance_prevout_vout`. Each reference must be complete or absent.
- Backfill each reference from the earliest legacy registration action, then contract extras, then cached legacy JSON. Validate values and report missing, malformed, and conflicting data counts.
- Preserve contracts, contract extras, historical actions, and asset timestamps; contract fields may affect the contract hash.
- Perform no network calls during migration or verification-disabled registration. Unavailable references remain nullable.
- Verified chain metadata takes precedence over explicitly supplied trusted-import metadata. Ordinary client extras do not populate these columns.
- Expose `{txid, vin}` and `{txid, vout}` objects through legacy and v2 responses. Legacy omits unknown values; v2 emits nulls.
- Apply the migration with registration writes paused, deploy the application, check backfill counts and representative responses, then resume writes.

## Implementation Modules

### Module 1 — Schema migration and backfill

- [x] Add nullable columns and pair, hash-format, and unsigned-index constraints.
- [x] Backfill all asset statuses by UUID in bounded batches, preserving source data.
- [x] Update existing legacy/v2 fragments and support downgrade without erasing legacy references.
- [x] Freeze revision `0007` projection logic so populated historical upgrades do not depend on future models.

### Module 2 — Registration and import

- [x] Return typed issuance metadata from successful Esplora verification using existing wallycore parsing.
- [x] Persist verified references with legacy/v2 registration and explicitly validated legacy imports.
- [x] Keep trusting verification network-free and client request formats unchanged.

### Module 3 — API, SDK, and documentation

- [x] Update registration, lookup, search, and cached/uncached export serializers.
- [x] Add typed v2 response properties and SDK legacy response fields.
- [x] Regenerate and check OpenAPI and SDK snapshots.
- [x] Document storage, source precedence, and missing-reference behavior.

### Module 4 — Validation and compatibility

- [x] Test fresh installation and populated historical upgrades, current-head upgrade, and downgrade.
- [x] Test malformed values, precedence, partial/missing data, constraints, batching, and repeated registrations.
- [x] Check registration/import persistence, migration preservation, and cached/uncached response parity.
- [x] Run the Python suite, OpenAPI alignment, and SDK checks.

Validation results: the full Python suite passed 397 tests; a final focused run passed 37 tests, including expanded historical-upgrade coverage. All 118 SDK tests passed, along with generated-type checks, lint, formatting, example type checks, and package build. Ruff and mypy comparisons against the original checkout introduced no new diagnostics (Ruff: 62 baseline / 56 current; mypy: 44 baseline / 44 current). All new Python modules and migration tests pass targeted Ruff checks. Migration execution used an isolated PostgreSQL 16 container.
