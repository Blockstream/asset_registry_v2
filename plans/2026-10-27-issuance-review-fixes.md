# Issuance Reference Review Fixes

Date: 2026-10-27

## Implementation Status

- `[x]` Complete
- `[~]` In progress
- `[ ]` Not started

Current progress:

- [x] Module 1 — Reconcile historical migrations through a forward revision.
- [x] Module 2 — Preserve compatibility and reduce duplicate validation.
- [x] Module 3 — Provide explicit chain backfill for missing local history.
- [x] Module 4 — Verify regressions, generated schemas, and SDK compatibility.
- [x] Module 5 — Address follow-up review notes.

## Purpose

Check and address the ten review candidates for commit `4564d59`.

## Design Decisions

| Candidate | Resolution |
| --- | --- |
| 1: Already-applied 0014 is skipped | New revision 0016 repeats provable repairs for existing databases. |
| 2: Cached timestamp spelling differs | 0016 normalizes v2 asset and admin-action timestamps, including historical 0007 output. Keep 0007 independent of live serializers. |
| 3: One bad legacy row blocks upgrades | Report and skip unprovable rows; preserve hash-chained actions. Test mixed genuine/synthesized null recovery. |
| 4: External extension keys rejected | Ignore unknown nested issuance fields while strictly validating the required values. Regenerate OpenAPI and SDK types. |
| 5: Old v2 references absent locally | Keep migrations offline; add a retryable, explicit Esplora maintenance command with dry-run and limit options. |
| 6: Fragment with only v2_json | Not reachable under the required `legacy_json NOT NULL` schema. Missing entire fragment rows already use live serializers. |
| 7: Migration depends on live application code | Freeze canonical encoding, wallycore hashing/asset derivation, and contract repair in a migration-only module. |
| 8: Legacy fields dropped if typed validation fails | Retain raw saved v1 fields when columns are unknown. 0016 restores recoverable fields removed by original 0015. Typed v2 values remain null. |
| 9: Repeated response validation | Build dictionaries directly from constrained database columns. Continue validating external input. |
| 10: Duplicate import parsing | Return parsed issuance metadata from the internal parser, preserving the existing public helper's return signature. |

Corrections cannot recover arbitrary null fields erased from all actions,
projections, and extras by the earliest 0014. Only candidates proven by issuance
identity or the preserved hash are accepted; unresolved rows remain reportable.

## Implementation Modules

### Module 1 — Forward migration

- [x] Share frozen, bounded contract repair between 0014 and new 0016.
- [x] Restore recoverable contract nulls and original uncompressed issuer keys.
- [x] Preserve historical asset timestamps and log skipped repairs.
- [x] Reconcile legacy issuance fields and cached timestamp representations.

### Module 2 — Runtime compatibility

- [x] Accept external extension fields without emitting them in typed responses.
- [x] Retain raw legacy history where typed references are unknown.
- [x] Avoid revalidating constrained database reference columns.
- [x] Parse issuance input once per import item, including dry runs.

### Module 3 — Chain maintenance

- [x] Add batched, retryable missing-reference verification and persistence.
- [x] Protect existing references and refresh cached responses atomically.
- [x] Document explicit invocation, dry runs, retries, and unavailable history.

### Module 4 — Validation

- [x] Add regression tests for applied 0014/0015 states and timestamp parity.
- [x] Add parsing, legacy compatibility, and maintenance command tests.
- [x] Run the full Python suite and SDK checks.
- [x] Check generated API snapshots and review the final diff.

Validation: 412 Python tests passed against isolated PostgreSQL 16 (requires
`ASSET_REGISTRY_TEST_DATABASE_URL` and `alembic upgrade head`; otherwise
database-backed tests are skipped); the final
migration-query refinement passed all 20 migration regression tests. All 118 SDK
tests, generated-type checks, formatting, lint, and application/example type
checks passed. OpenAPI and SDK types were regenerated; the generated SDK file was
unchanged. Focused Ruff checks passed. Mypy reports the same 44 baseline
diagnostics with no new diagnostics. Frozen repair reproduced all 40,534 original
contracts in the checked-in production/testnet datasets from simulated old
0014 output. A local 10,000-row projection timing measured 0.008s for direct
dictionaries versus 0.030s for validating and dumping reference models.

### Module 5 — Follow-up review notes

- [x] Write migration fragments as compact UTF-8 JSON to match the live serializer.
- [x] Skip 0016 fragment updates whose text is unchanged.
- [x] Keep skipped unprovable assets fully untouched and backfill without audit
  actions; both remain documented operator-facing decisions.
- [x] Add a regression test for compact output and unchanged-row skipping; 413
  Python tests pass against PostgreSQL 16.
