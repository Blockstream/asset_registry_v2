# Content-Addressed v2 Asset Icons

Date: 2026-07-31

## Implementation Status

- `[x]` Complete
- `[~]` In progress
- `[ ]` Not started

Current progress:

- `[x]` Module 1 - Replace embedded v2 icon bytes with descriptors
- `[x]` Module 2 - Add content-addressed icon retrieval
- `[x]` Module 3 - Migrate caches and align clients

## Purpose

Keep v2 asset metadata responses compact while preserving convenient icon
discovery. Asset responses link to registry-owned, content-addressed PNG
resources; `/icons.json` remains available for callers that prefer a bulk map.

## Design Decisions

- Return `icon` on every v2 asset as either `{ "href": "..." }` or `null`.
- Put the approved icon's SHA-256 hash in its resource path rather than exposing
  a separate response field.
- Keep previously published, retained icon bytes available at their immutable
  hashed URLs when the asset's current icon changes.
- Keep an unversioned current-icon route that redirects to the hashed resource.

## Implementation Modules

### Module 1 - Replace embedded v2 icon bytes with descriptors

- `[x]` Update dynamic and cached v2 projections for native and legacy assets.
- `[x]` Directly replace the initial Base64 response field.

### Module 2 - Add content-addressed icon retrieval

- `[x]` Add current and hash-addressed PNG endpoints.
- `[x]` Add ETag, conditional request, and immutable cache behavior.

### Module 3 - Migrate caches and align clients

- `[x]` Add reversible cached-fragment migration.
- `[x]` Regenerate OpenAPI and SDK types and update examples.
- `[x]` Add response, endpoint, migration, and cache regression coverage.
