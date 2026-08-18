# Asset Registry Service Implementation Plan

This plan breaks the work into implementation modules for a FastAPI + PostgreSQL service that preserves the legacy v1 API surface and adds the v2 registry model from the OpenAPI contract.

The plan assumes:

- FastAPI is the HTTP framework.
- PostgreSQL is the durable store.
- Alembic is used for migrations.
- The legacy root API remains available at the base path.
- New API behavior lives under `/v2`.
- `asset_id` is the Liquid blockchain asset identifier, not the database primary key.
- `asset_uuid` is the internal UUID identifier for asset records.
- Audit responses are projected from an append-only `actions` table.
- State hashes are omitted.
- v1-to-v2 migration means "move this asset into the v2 registry model," not "retroactively make this a native v2 contract."
- v1-to-v2 migration should be simple and should not include a migration payload unless a concrete need appears later.

## Implementation Status

Last updated: 2026-04-30

Legend:

- `[x]` completed and verified locally
- `[~]` implemented or partially verified, with remaining integration work
- `[ ]` not started

Current progress:

- `[x]` Module 1 - Service Scaffold
  - `[x]` FastAPI application package.
  - `[x]` Environment-based configuration.
  - `[x]` Health check endpoint.
  - `[x]` FastAPI Swagger/OpenAPI docs endpoint.
  - `[x]` Basic pytest harness.
  - `[x]` Dockerfile for the API service.
  - `[x]` `docker-compose.yml` with API and PostgreSQL services.
  - `[x]` Local API process starts outside Docker and `/health` returns success.
  - `[x]` API starts locally through Docker Compose.
  - `[x]` Swagger UI verified through Docker Compose.
- `[x]` Module 2 - Database Schema and Migrations
  - `[x]` SQLAlchemy model layer for the proposed tables.
  - `[x]` Alembic setup.
  - `[x]` Initial migration for `assets`, mutable metadata tables, admin annotations, issuer key history, and `actions`.
  - `[x]` Required indexes and constraints represented in the migration.
  - `[x]` Offline upgrade SQL generation verified.
  - `[x]` Offline downgrade SQL generation verified.
  - `[x]` Migration applied against a live empty PostgreSQL database.
  - `[x]` Migration rollback tested against a live PostgreSQL database.
  - `[x]` Constraint rejection tests against PostgreSQL.
- `[x]` Module 3 - Shared Domain and Validation Layer
  - `[x]` Pydantic models for legacy and v2 request/response shapes.
  - `[x]` Asset ID, pubkey, ticker, name, precision, domain, and URL validators.
  - `[x]` Contract canonicalization and contract hash utilities.
  - `[x]` Canonical JSON utilities for signed v2 actions.
  - `[x]` Domain verification support for `http` and `dns`, with default `http`.
  - `[x]` Chain verification abstraction for issuance commitment checks.
  - `[x]` Error response model aligned with the OpenAPI contract.
  - `[x]` Contract hash, canonical JSON, validation, and mocked domain verification tests.
- `[x]` Module 4 - Legacy v1 Registration
  - `[x]` Parse legacy registration request.
  - `[x]` Verify issuance commitment and domain proof through service-layer hooks.
  - `[x]` Insert an `assets` row with legacy-compatible metadata.
  - `[x]` Insert default mutable/admin rows.
  - `[x]` Insert registration action.
  - `[x]` Preserve the legacy response shape where practical.
  - `[x]` Valid legacy asset registers successfully.
  - `[x]` Duplicate active namespace is rejected through database constraints.
  - `[x]` Failed domain proof and chain verification are rejected when enforcement hooks are enabled.
  - `[x]` Wire concrete chain verifier backend for issuance data.
  - `[x]` Wire concrete HTTP/DNS domain verifier backends for enforced production mode.
  - `[x]` Network selection is configurable through `ASSET_REGISTRY_NETWORK`; default is `liquid`.
- `[x]` Module 5 - Legacy Compatibility Write Gate
  - `[x]` Env-gated synchronous v1 registration forwarding before local v2 writes.
  - `[x]` Optional write-through forwarding to the original registry base URL.
  - `[x]` V2 write is blocked when v1 does not confirm the registration.
  - `[x]` V1 failure sanity check via `GET /{asset_id}` for known false-failure responses.
  - `[x]` Structured logging of shadow outcomes.
  - `[x]` Unit coverage for disabled writes, confirmed legacy 500s, and unconfirmed legacy failures.
- `[x]` Module 6 - Legacy v1 Lookup and Listing
  - `[x]` `GET /{asset_id}` for active legacy asset responses.
  - `[x]` `GET /` listing for active legacy assets as a v1-compatible object keyed by `asset_id`.
  - `[x]` Legacy response reconstruction from registration actions or projected asset columns.
  - `[x]` Missing/deregistered assets excluded from active reads.
  - `[x]` Live PostgreSQL coverage for lookup and listing.
- `[x]` Module 7 - Legacy v1 Deregistration
  - `[x]` `DELETE /{asset_id}`.
  - `[x]` Legacy Bitcoin-message secp256k1 deletion signature verification.
  - `[x]` Assets are marked `deregistered` instead of deleted.
  - `[x]` Deregistration action is inserted with verified pubkey and signature.
  - `[x]` Live PostgreSQL coverage for valid and invalid signatures.
- `[x]` Module 8 - v1-to-v2 Registry Migration
  - `[x]` Dedicated `POST /v2/assets/{asset_id}/migrate` migration endpoint.
  - `[x]` Simple migration path without required payload.
  - `[x]` Marks migrated legacy assets with `initial_issuer_pubkey_source = migrated_legacy_record`.
  - `[x]` Inserts append-only `migrate_contract_metadata` action.
  - `[x]` Repeated migration returns `idempotent_retry`.
  - `[x]` Live PostgreSQL coverage for migration and idempotency.
- `[x]` Module 9 - v2 Registration
  - `[x]` `POST /v2/assets`.
  - `[x]` Parse `RegisterAssetRequest`.
  - `[x]` Enforce `contract.initial_issuer_pubkey` for v2 contracts.
  - `[x]` Default `domain_verification_method` to `http`.
  - `[x]` Verify chain commitment and domain proof through service-layer hooks.
  - `[x]` Insert asset, mutable metadata tables, admin annotations, issuer key history, and registration action in one transaction.
  - `[x]` Return `AssetResponse`.
  - `[x]` Live PostgreSQL coverage for registration and duplicate conflicts.
- `[x]` Module 10 - v2 Search, Listing, and Lookup
  - `[x]` `GET /v2/assets`.
  - `[x]` `GET /v2/assets/all.json`.
  - `[x]` `GET /v2/assets/{asset_id}`.
  - `[x]` Page-number pagination.
  - `[x]` Sort support.
  - `[x]` Search by `asset_id_prefix`, `domain`, `ticker`, `name`, `asset_type`, `category_tag`, and `trading_venue`.
  - `[x]` `include_deregistered` behavior.
  - `[x]` Response assembly from normalized tables.
  - `[x]` Deterministic lookup ordering if historical duplicates exist.
  - `[x]` Live PostgreSQL coverage for lookup, filters, pagination, all.json, and deregistered visibility.
- `[x]` Module 11 - v2 Issuer Actions
  - `[x]` `POST /v2/assets/{asset_id}/actions`.
  - `[x]` Signature header parsing.
  - `[x]` Canonical request-body enforcement.
  - `[x]` URL/body `asset_id` match enforcement.
  - `[x]` Timestamp freshness checks.
  - `[x]` Nonce uniqueness and idempotent retry behavior through `actions`.
  - `[x]` Explicit issuer operations for `replace_category_tags`, `replace_trading_venues`, `replace_custom`, `set_custom_field`, and `delete_custom_field` after the 2026-05-05 payload refactor.
  - `[x]` `deregister` operation.
  - `[x]` Transactional state mutation plus action insertion.
  - `[x]` Live PostgreSQL coverage for valid mutations, non-canonical payloads, invalid signatures, stale timestamps, nonce replay, and nonce conflicts.
- `[x]` Module 12 - Issuer Key Rotation
  - `[x]` `rotate_issuer_pubkey` issuer action.
  - `[x]` Verify action with current issuer key.
  - `[x]` Update `assets.current_issuer_pubkey`.
  - `[x]` Close current `issuer_pubkey_history` row and open a new one.
  - `[x]` Insert rotation action.
  - `[x]` Live PostgreSQL coverage for rotation, old-key rejection after rotation, new-key acceptance after rotation, and key history projection.
- `[x]` Module 13 - Admin Annotations
  - `[x]` `PUT /v2/admin/assets/{asset_id}/annotations`.
  - `[x]` Admin bearer-token authentication hook.
  - `[x]` Asset type, featured, malicious, delisted, and admin notes updates.
  - `[x]` Admin action insertion.
  - `[x]` `last_admin_action_uuid` update and response projection.
  - `[x]` Live PostgreSQL coverage for auth hook, updates, and `admin.last_admin_action`.
- `[x]` Module 14 - Audit Projection
  - `[x]` `GET /v2/assets/{asset_id}/audit`.
  - `[x]` `GET /v2/audit`.
  - `[x]` `audit_sequence` mapped to API `audit_id`.
  - `[x]` `since_audit_id` and `limit` pagination.
  - `[x]` Asset-specific and global audit filters.
  - `[x]` Operation, actor, and server timestamp filters.
  - `[x]` No state hashes in responses.
  - `[x]` Live PostgreSQL coverage for ordering, pagination, asset-specific audit, and global filters.
- `[x]` Module 15 - OpenAPI Spec Alignment
  - `[x]` Removed state hashes from the authored specification.
  - `[x]` Removed migration payloads from v1-to-v2 migration behavior and the authored specification.
  - `[x]` Clarified v1-to-v2 migration semantics.
  - `[x]` Clarified default `domain_verification_method`.
  - `[x]` Clarified duplicate `asset_id` behavior.
  - `[x]` Added OpenAPI alignment tests for authored and generated docs.
- `[x]` Module 16 - Operational Readiness
  - `[x]` Structured JSON request logging.
  - `[x]` Request IDs with `X-Request-ID` propagation.
  - `[x]` Database transaction boundary and retry policy documented.
  - `[x]` Configuration documentation.
  - `[x]` Local seed/test fixture workflow documentation.
  - `[x]` CI workflow for migrations, tests, and OpenAPI parsing.
  - `[x]` Deployment notes.

## Module 1 - Service Scaffold

Create the FastAPI service skeleton and local development environment.

Deliverables:

- FastAPI application package.
- Dockerfile for the API service.
- `docker-compose.yml` with API and PostgreSQL services.
- Environment-based configuration for database URL, service host/port, and development settings.
- Health check endpoint.
- Swagger/OpenAPI docs exposed through FastAPI's built-in docs endpoint.
- Basic test harness.

Tests:

- API starts locally through Docker Compose.
- Health endpoint returns success.
- Swagger UI loads and shows registered routes.

## Module 2 - Database Schema and Migrations

Create the PostgreSQL schema from `schema.md`.

Deliverables:

- SQLAlchemy models or equivalent database model layer.
- Alembic setup.
- Initial migration for:
  - `assets`
  - `asset_mutable_metadata`
  - `asset_trading_venues`
  - `asset_category_tags`
  - `asset_custom_attributes`
  - `asset_admin_annotations`
  - `issuer_pubkey_history`
  - `actions`
- Required indexes and constraints.
- Seed-free controlled values, with validation handled in code.

Tests:

- Migrations apply cleanly to an empty database.
- Migrations roll back cleanly where practical.
- Schema constraints reject malformed UUIDs, asset IDs, and public keys. Controlled strings are rejected by service-layer validation.

## Module 3 - Shared Domain and Validation Layer

Implement reusable validation, canonicalization, and chain/domain proof helpers before wiring endpoints.

Deliverables:

- Pydantic models for legacy and v2 request/response shapes.
- Asset ID, pubkey, ticker, name, precision, domain, and URL validators.
- Contract canonicalization and contract hash utilities.
- Canonical JSON utilities for signed v2 actions.
- Domain verification support for `http` and `dns`, with default `http`.
- Chain verification abstraction for issuance commitment checks.
- Error response model aligned with the OpenAPI contract.

Tests:

- Contract hash tests against known fixture values.
- Canonical JSON round-trip tests.
- Validation tests for accepted and rejected request payloads.
- Domain verification helper tests with mocked HTTP/DNS.

## Module 4 - Legacy v1 Registration

Implement the legacy root registration flow using FastAPI and PostgreSQL instead of filesystem/git storage.

Endpoints:

- `POST /`

Deliverables:

- Parse legacy registration request.
- Verify issuance commitment and domain proof.
- Insert an `assets` row with legacy-compatible metadata.
- Insert default mutable/admin rows.
- Insert registration action.
- Preserve the legacy response shape where practical.

Tests:

- Valid legacy asset registers successfully.
- Duplicate active namespace is rejected when legacy uniqueness rules require it.
- Invalid contract hash, invalid issuance data, and failed domain proof are rejected.

## Module 5 - Legacy Compatibility Write Gate

Add optional migration support that forwards legacy requests to the original registry before writing locally. The legacy registry is the write gate while the domain is pointed at the v2 service.

Configuration:

- `ASSET_REGISTRY_LEGACY_BASE_URL`
- `ASSET_REGISTRY_LEGACY_SHADOW_WRITE`
- `ASSET_REGISTRY_LEGACY_COMPARE_RESPONSES`
- `ASSET_REGISTRY_LEGACY_TIMEOUT_SECONDS`

Deliverables:

- Synchronous v1 registration forwarding before local v2 persistence.
- Local v2 write only after v1 confirms the registration.
- Sanity check `GET /{asset_id}` after non-2xx or ambiguous v1 registration responses.
- Outcome classification:
  - `legacy_write_succeeded`
  - `legacy_write_confirmed_after_failure`
  - `legacy_write_failed`
  - `legacy_unreachable`
  - `shadow_disabled`
- Structured logging or table-backed recording of shadow outcomes.
- Legacy failures block local v2 writes unless the sanity check confirms the asset exists in v1.

Tests:

- V1 forwarding runs only when enabled.
- Legacy 500 with `GET /{asset_id}` confirmation allows the local v2 write.
- Legacy 500 without `GET /{asset_id}` confirmation blocks the local v2 write.
- Legacy unreachable blocks the local v2 write.

## Module 6 - Legacy v1 Lookup and Listing

Implement legacy read endpoints.

Endpoints:

- `GET /`
- `GET /{asset_id}`

Deliverables:

- Legacy single-asset response assembly.
- Legacy all-assets response assembly.
- Compatibility behavior for missing assets.
- Query behavior for current active records when duplicate historical `asset_id` rows exist.

Tests:

- Registered asset can be fetched by `asset_id`.
- Missing asset returns legacy-compatible 404.
- All-assets endpoint returns the expected v1 object shape keyed by `asset_id`.

## Module 7 - Legacy v1 Deregistration

Implement legacy deletion/deregistration behavior using database state.

Endpoints:

- `DELETE /{asset_id}`

Deliverables:

- Parse and verify legacy deletion signature.
- Mark asset status as `deregistered`.
- Insert deregistration action.
- Preserve historical data instead of deleting rows.
- Ensure deregistered assets are excluded from default legacy listing behavior.

Tests:

- Valid issuer deletion signature deregisters the asset.
- Invalid signature is rejected.
- Deregistered asset no longer appears in default legacy listing.

## Module 8 - v1-to-v2 Registry Migration

Implement migration of existing v1 registry records into the v2 registry model.

Endpoint:

- `POST /v2/assets/{asset_id}/migrate`

Deliverables:

- Use a dedicated migration route until the OpenAPI spec finalizes issuer-action migration shape.
- Treat migration as registry-model migration, not native v2 contract recreation.
- Do not require a migration payload for the simple case.
- Set `initial_issuer_pubkey_source = migrated_legacy_record` where appropriate.
- Preserve original contract metadata in the migration action payload when needed for audit/reconstruction.
- Insert migration action.

Tests:

- Legacy asset can be marked as v2-managed.
- Migration does not change blockchain identity or the registered issuance commitment.
- Repeated migration is idempotent or rejected consistently.

## Module 9 - v2 Registration

Implement native v2 asset registration.

Endpoints:

- `POST /v2/assets`

Deliverables:

- Parse `RegisterAssetRequest`.
- Enforce `contract.initial_issuer_pubkey` for v2 contracts.
- Default `domain_verification_method` to `http` if omitted by implementation policy or finalized spec.
- Verify chain commitment and domain proof.
- Insert asset, mutable metadata tables, admin annotations, issuer key history, and registration action in one transaction.
- Return `AssetResponse`.

Tests:

- Valid v2 asset registers successfully.
- Missing `initial_issuer_pubkey` on v2 contract is rejected.
- Legacy-style registration through the v2 endpoint follows the finalized compatibility rules.
- Mutable metadata is normalized into child tables and reconstructed in the response.

## Module 10 - v2 Search, Listing, and Lookup

Implement v2 read endpoints.

Endpoints:

- `GET /v2/assets`
- `GET /v2/assets/all.json`
- `GET /v2/assets/{asset_id}`

Deliverables:

- Page-number pagination.
- Sort support.
- Search by `asset_id_prefix`, `domain`, `ticker`, `name`, `asset_type`, `category_tag`, and `trading_venue`.
- `include_deregistered` behavior.
- Response assembly from normalized tables.
- Deterministic behavior if multiple records share a blockchain `asset_id`.

Tests:

- Filters use the intended indexes.
- Pagination is stable.
- Normalized category and venue searches return expected results.
- `all.json` response is compatible with the spec.

## Module 11 - v2 Issuer Actions

Implement the signed issuer action pipeline.

Endpoint:

- `POST /v2/assets/{asset_id}/actions`

Deliverables:

- Signature header parsing.
- Canonical request-body enforcement.
- URL/body `asset_id` match enforcement.
- Timestamp freshness checks.
- Nonce uniqueness and idempotent retry behavior through `actions`.
- Explicit issuer metadata operations:
  - `replace_category_tags`
  - `replace_trading_venues`
  - `replace_custom`
  - `set_custom_field`
  - `delete_custom_field`
- `deregister` operation.
- Transactional state mutation plus action insertion.

Tests:

- Non-canonical request bodies are rejected.
- Valid signatures are accepted.
- Invalid signatures are rejected.
- Nonce replay with same payload returns idempotent retry.
- Nonce replay with different payload is rejected.
- Mutable metadata tables are updated correctly.

## Module 12 - Issuer Key Rotation

Implement key rotation as a signed v2 issuer action.

Endpoint:

- `POST /v2/assets/{asset_id}/actions` with `operation = rotate_issuer_pubkey`

Deliverables:

- Verify action with current issuer key.
- Optionally verify possession of the new key if the finalized spec keeps `new_issuer_pubkey_signature`.
- Update `assets.current_issuer_pubkey`.
- Close current `issuer_pubkey_history` row and open a new one.
- Insert rotation action.

Tests:

- Current key can rotate to a valid new key.
- Old key cannot sign subsequent actions after rotation.
- New key can sign subsequent actions.
- Key history response derives correct audit sequence values.

## Module 13 - Admin Annotations

Implement admin-operated metadata and moderation.

Endpoint:

- `PUT /v2/admin/assets/{asset_id}/annotations`

Deliverables:

- Admin authentication hook or placeholder integration.
- Update asset type, featured, malicious, delisted, and admin notes.
- Insert admin action.
- Set `last_admin_action_uuid`.
- Return updated `AssetResponse`.

Tests:

- Unauthorized request is rejected.
- Authorized request updates annotations.
- `admin.last_admin_action` is assembled from the referenced action.

## Module 14 - Audit Projection

Implement audit log endpoints as projections over `actions`.

Endpoints:

- `GET /v2/assets/{asset_id}/audit`
- `GET /v2/audit`

Deliverables:

- `audit_sequence` mapped to API `audit_id`.
- `since_audit_id` and `limit` pagination.
- Asset-specific and global audit filters.
- Operation, actor, and server timestamp filters.
- Audit response shape aligned with the OpenAPI contract.
- No state hashes in responses.

Tests:

- Audit entries are ordered by `audit_sequence`.
- Pagination resumes correctly with `next_since_audit_id`.
- Asset-specific audit returns only matching actions.
- Global audit filters work together.

## Module 15 - OpenAPI Spec Alignment

Update the authored OpenAPI specification to match implementation decisions.

Deliverables:

- Remove `previous_state_hash` and `resulting_state_hash`.
- Decide whether `migration_payload` is removed from migration actions.
- Clarify v1-to-v2 migration semantics.
- Clarify default `domain_verification_method`.
- Clarify duplicate `asset_id` behavior.
- Ensure generated FastAPI docs and the authored OpenAPI specification do not conflict.

Tests:

- OpenAPI schema validates.
- Example requests match implemented behavior.
- FastAPI-generated route docs are reviewed against the authored OpenAPI specification.

## Module 16 - Operational Readiness

Add production-facing support.

Deliverables:

- Structured logging.
- Request IDs.
- Database transaction boundaries and retry policy.
- Configuration documentation.
- Local seed/test fixture workflow.
- CI checks for linting, tests, and migrations.
- Deployment notes.

Tests:

- Full integration test suite runs in Docker Compose.
- Migrations run in CI against PostgreSQL.
- Registration, action submission, search, and audit work end-to-end.

## Suggested Build Order

1. Modules 1-3 establish the platform and shared correctness primitives.
2. Module 4 starts the existing v1 API on the new stack.
3. Module 5 adds migration-only shadow compatibility checks against the original registry.
4. Modules 6-7 complete the remaining legacy v1 API behavior.
5. Modules 8-10 add the v2 asset model and read surface.
6. Modules 11-12 add signed issuer mutability and key rotation.
7. Modules 13-14 complete admin and audit behavior.
8. Modules 15-16 tighten the spec and production posture.

## Spec Questions to Resolve Before Coding v2 Actions

- Should `migrate_contract_metadata` remain an issuer action, an admin/system action, or both?
- Should `migration_payload` be removed entirely for simple v1-to-v2 registry migration?
- Should `new_issuer_pubkey_signature` be required, optional, or removed?
- Should `domain_verification_method` become optional in the OpenAPI request with server default `http`?
- What should `GET /v2/assets/{asset_id}` do if more than one active row has the same blockchain `asset_id`?
