# Security Hardening Follow-Up Plan

Date: 2026-05-21

## Implementation Status

Legend:

- `[x]` completed and verified locally where possible
- `[~]` implemented or partially verified, with remaining integration work
- `[ ]` not started

Current progress:

- `[x]` Module 1 - Request Body Size Limits
- `[x]` Module 2 - Domain Proof Fetch Hardening
- `[x]` Module 3 - Legacy Extra Field Size Limits
- `[x]` Module 4 - Migration Endpoint Authorization
- `[x]` Module 5 - Production Container Cleanup
- `[x]` Module 6 - Tests and Verification
- `[x]` Module 7 - Documentation and Deployment Notes

## Purpose

Address the practical security concerns found during the registry API security sweep.

This plan intentionally focuses on application and deployment hardening that is relevant to this service, rather than scanner findings that only report vulnerable transitive packages without a reachable application path.

## Scope

In scope:

- Limit inbound request body size before expensive parsing or persistence.
- Reduce SSRF and internal-network exposure from HTTP domain proof fetching.
- Add explicit size limits for legacy registration extra fields.
- Decide and implement authorization for the legacy-to-v2 migration endpoint.
- Clean up the production container image so it does not install dev extras and does not run as root.

Out of scope:

- Reworking the registry trust model.
- Replacing HTTP/DNS domain proofs.
- Adding rate limiting as a full feature. Rate limiting may be noted as deployment follow-up.
- Addressing third-party OS CVEs that require upstream base image fixes.

## Design Decisions

Prefer defense in depth:

- Enforce request size at the reverse proxy and in the app.
- Keep HTTP proof response size limits, but also constrain where the app can connect.
- Preserve legacy compatibility where possible, but make unbounded legacy fields bounded.
- Treat migration as a state-changing operation. If migration remains public, that should be a deliberate product decision rather than an accident.
- Keep the production image minimal and separate from local development conveniences.

## Implementation Modules

### Module 1 - Request Body Size Limits - Completed

- Add an application-level request body size limit middleware.
- Make the limit configurable through settings, for example `ASSET_REGISTRY_MAX_REQUEST_BODY_BYTES`.
- Choose an initial limit that comfortably covers expected registration/admin/action payloads while rejecting abusive requests.
- Return a clear `413 Payload Too Large` response before reading or parsing oversized bodies.
- Ensure routes that call `await request.body()` for signed payloads are covered.
- Add or document reverse proxy limits, such as nginx `client_max_body_size`, as the first line of defense.

### Module 2 - Domain Proof Fetch Hardening - Completed

- Keep the existing 10 KiB HTTP proof response cap.
- Add safeguards for user-controlled HTTP proof destinations:
  - Resolve the proof domain before fetching.
  - Reject private, loopback, link-local, multicast, and otherwise non-public IP targets.
  - Handle both IPv4 and IPv6.
  - Consider whether `.onion` should remain exempt from normal DNS/IP checks because it intentionally uses Tor routing.
- Disable or carefully control redirects for proof fetches, or re-check the resolved target after redirects.
- Keep timeout behavior strict.
- Document that production deployments should also restrict container egress to expected internet destinations.

### Module 3 - Legacy Extra Field Size Limits - Completed

- Preserve `extra="allow"` for legacy compatibility unless a stronger compatibility decision is made.
- Add explicit limits for legacy request and legacy contract extras:
  - Maximum total serialized request size.
  - Maximum number of extra keys.
  - Maximum serialized size per extra value.
- Reuse the v2 custom metadata size-limit style where practical.
- Ensure extra fields remain included in legacy contract hash calculation when accepted.
- Add tests for oversized base64-like extra fields, too many keys, and accepted small extra fields.

### Module 4 - Migration Endpoint Authorization - Completed

- Decide whether `POST /v2/assets/{asset_id}/migrate` should be admin-only.
- Recommended implementation: require signed admin authorization with a dedicated permission or an existing permission that clearly fits.
- Candidate permission: `migrate_assets`.
- If adding a new permission, update:
  - Validation constants.
  - Admin permission schemas.
  - OpenAPI/docs.
  - Tests for permission enforcement.
- Keep migration idempotent for repeated authorized calls.
- If the endpoint intentionally remains public, document that decision and add an explicit test showing public migration is intended.

### Module 5 - Production Container Cleanup - Completed

- Move runtime dependencies out of dev extras.
  - `httpx` is used by the application and should be a runtime dependency.
  - Keep test-only dependencies under `[project.optional-dependencies].dev`.
- Change the production Dockerfile to install `"."` instead of `".[dev]"`.
- Add a non-root runtime user.
- Avoid mounting the source tree or using `--reload` in production examples.
- Consider a multi-stage Dockerfile if build tooling or caches become noisy.
- Keep local `docker-compose.yml` development-friendly, but label it clearly as development-only or add a separate production compose/deployment example.

### Module 6 - Tests and Verification - Completed

- Add focused tests for request body size middleware.
- Add HTTP proof fetch tests for:
  - Oversized responses.
  - Private IPv4 targets.
  - Private IPv6 targets.
  - Redirects to disallowed targets if redirects are supported.
- Add legacy extra field limit tests.
- Add migration authorization tests.
- Build the Docker image locally after dependency changes.
- Run the focused test files and a broader registry API test pass where the local database setup allows it.

### Module 7 - Documentation and Deployment Notes - Completed

- Document new settings and recommended defaults.
- Document reverse proxy body-size configuration.
- Document production egress expectations for HTTP/DNS proof verification.
- Document migration authorization behavior.
- Document that local `docker-compose.yml` is for development if production compose is not added.

## Open Questions

- App-level body size limit is currently 1 MiB through `ASSET_REGISTRY_MAX_REQUEST_BODY_BYTES`.
- `.onion` HTTP proof targets currently skip public DNS/IP checks because they intentionally do not resolve through normal DNS.
- Legacy extra fields are bounded independently: 32 extra keys, 2 KiB per extra value, and 16 KiB total legacy request size.
- Migration now uses a dedicated `migrate_assets` permission, with `root` still satisfying the permission check.
- Production container hardening was applied to the existing `Dockerfile`; a separate dev Dockerfile can still be added later if needed.
