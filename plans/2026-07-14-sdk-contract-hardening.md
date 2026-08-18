# SDK Contract and Packaging Hardening

Date: 2026-07-14

## Implementation Status

Legend:

- `[x]` completed and verified locally where possible
- `[~]` implemented or partially verified, with remaining integration work
- `[ ]` not started

Current progress:

- `[x]` Module 1 - Generated API Types and Package Build
- `[x]` Module 2 - Shared Transport and Error Handling
- `[x]` Module 3 - Signing and Validation Cleanup
- `[x]` Module 4 - Client Contract Alignment
- `[x]` Module 5 - Tests, Documentation, and Package Verification

## Purpose

Align the initial TypeScript SDK with the FastAPI-generated OpenAPI contract, remove misleading APIs, and make the published ESM, CommonJS, and declaration outputs reproducible.

## Design Decisions

- `openapi.yaml` is the source of truth for HTTP wire types.
- Generated OpenAPI types remain internal; the SDK exports curated semantic aliases and ergonomic camel-case query option types.
- Signed requests may be retried with the exact same canonical body, nonce, and signature. Registration and legacy writes are not retried automatically.
- Incorrect initial public interfaces are corrected directly without compatibility shims.

## Implementation Modules

### Module 1 - Generated API Types and Package Build

- `[x]` Generate and check committed TypeScript wire types from `openapi.yaml`.
- `[x]` Repair TypeScript emit settings and runtime-safe relative imports.
- `[x]` Produce loadable ESM, CommonJS, and declaration builds.
- `[x]` Correct package exports and dependency declarations.

### Module 2 - Shared Transport and Error Handling

- `[x]` Share configured transport instances across clients.
- `[x]` Honor timeouts and retries with safe per-request retry policy.
- `[x]` Parse registry and framework error envelopes without exposing raw bodies by default.

### Module 3 - Signing and Validation Cleanup

- `[x]` Consolidate Bitcoin Signed Message signing logic.
- `[x]` Remove unimplemented or ignored signing APIs.
- `[x]` Tighten registry identifier validation and canonical JSON behavior.

### Module 4 - Client Contract Alignment

- `[x]` Correct asset search, lookup, listing, action, audit, and history behavior.
- `[x]` Move signed migration to the admin client.
- `[x]` Align raw action inputs and all response types with the wire contract.

### Module 5 - Tests, Documentation, and Package Verification

- `[x]` Add contract-focused client and transport coverage.
- `[x]` Update examples and SDK documentation.
- `[x]` Run formatting, linting, type, generated-type, test, build, and package smoke checks.
