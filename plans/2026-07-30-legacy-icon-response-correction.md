# Legacy Icon Response Correction

Date: 2026-07-30

## Implementation Status

- `[x]` Complete
- `[~]` In progress
- `[ ]` Not started

Current progress:

- `[x]` Module 1 - Correct legacy response serialization
- `[x]` Module 2 - Clean cached legacy fragments
- `[x]` Module 3 - Align SDK, tests, and documentation

## Purpose

Keep approved icon bytes out of legacy asset objects returned by `GET /` and
`GET /{asset_id}`. Approved icons remain available through `/icons.json` and v2
asset responses, while a genuine legacy contract extra field named `icon`
continues to be returned as `contract.icon`.

## Design Decisions

- Remove only the top-level `icon` property from legacy asset responses.
- Preserve native icon storage, review, import, v2 publication, and `/icons.json`.
- Clean already-materialized legacy response fragments during database upgrade.

## Implementation Modules

### Module 1 - Correct legacy response serialization

- `[x]` Remove approved icon injection from the shared legacy serializer.
- `[x]` Preserve arbitrary legacy contract fields, including `contract.icon`.

### Module 2 - Clean cached legacy fragments

- `[x]` Add a reversible migration that removes only top-level cached icons.

### Module 3 - Align SDK, tests, and documentation

- `[x]` Remove the top-level icon property from the SDK legacy response type.
- `[x]` Add response-shape and migration regression coverage.
- `[x]` Correct the native icon publication plan.
