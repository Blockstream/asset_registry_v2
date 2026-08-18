# Native Asset Icon Support

Date: 2026-07-17

## Implementation Status

- `[x]` Complete
- `[~]` In progress
- `[ ]` Not started

Current progress:

- `[x]` Module 1 - Storage and validation
- `[x]` Module 2 - Issuer proposals and audit
- `[x]` Module 3 - Admin review and publication
- `[x]` Module 4 - Legacy import and SDK
- `[x]` Module 5 - Verification and documentation

## Purpose

Add signed v2 issuer icon proposals, signed admin review, auditable state transitions, compact binary storage,
legacy-compatible publication, and TypeScript SDK support. A later domain-proof v1 submission flow can reuse the
same validation, storage, review, and publication services.

## Design Decisions

- Hash decoded PNG bytes with SHA-256; Base64 is only the JSON transport.
- New proposals must be 500x500 PNGs, under 75,000 decoded bytes, with an alpha channel.
- Keep at most one non-obsolete pending proposal per asset registration.
- Select the published icon through an explicit asset foreign key.
- Retain approved image bytes for later reuse; keep obsolescence orthogonal to review status.
- Grandfather valid PNGs imported from the legacy `icons.json` map.

## Implementation Modules

### Module 1 - Storage and validation

- `[x]` Add proposal schema, constraints, indexes, and PNG validation.

### Module 2 - Issuer proposals and audit

- `[x]` Add signed proposal envelope and issuer action-hash-chain integration.

### Module 3 - Admin review and publication

- `[x]` Add signed pending search and approve/reject actions.
- `[x]` Publish approved icons through content-addressed v2 links and `/icons.json`.

### Module 4 - Legacy import and SDK

- `[x]` Add an idempotent legacy-map importer with dry-run support.
- `[x]` Add SDK proposal, review, listing, hashing, and response types.
- `[x]` Add permission-separated direct admin upload and assignment support.

### Module 5 - Verification and documentation

- `[x]` Add database/API/importer/SDK coverage and regenerate OpenAPI.
