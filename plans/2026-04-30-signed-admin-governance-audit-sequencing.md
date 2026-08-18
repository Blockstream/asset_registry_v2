# Signed Admin Governance and Audit Sequencing Plan

Date: 2026-04-30

## Implementation Status

Legend:

- `[x]` completed and verified locally where possible
- `[~]` implemented or partially verified, with remaining integration work
- `[ ]` not started

Current progress:

- `[x]` Module 1 - Shared Audit Sequence
  - `[x]` Added `audit_sequence_global`.
  - `[x]` Migrated `actions.audit_sequence` to use the global sequence.
  - `[x]` Preserves existing audit IDs and seeds the global sequence above existing action rows.
  - `[x]` Added regression coverage for monotonic ordering across asset and admin actions.
- `[x]` Module 2 - Admin Tables
  - `[x]` Added `admin_keys`, `admin_permissions`, and `admin_actions`.
  - `[x]` Added SQLAlchemy models.
  - `[x]` Added migration constraint/index coverage for admin pubkey and nonce uniqueness.
- `[x]` Module 3 - Genesis Admin Bootstrap
  - `[x]` Added `ASSET_REGISTRY_GENESIS_ADMIN_PUBKEY`.
  - `[x]` Seeds one root genesis admin only when no admins exist.
  - `[x]` Validates genesis pubkey format with libwally.
- `[x]` Module 4 - Signed Admin Action Pipeline
  - `[x]` Added canonical admin action schemas.
  - `[x]` Verifies `Asset-Registry-Admin-Signature` as a recoverable secp256k1 signature.
  - `[x]` Enforces timestamp freshness and per-admin timestamp monotonicity.
  - `[x]` Enforces nonce idempotency/conflict behavior for admin lifecycle actions.
  - `[x]` Inserts `admin_actions` for admin lifecycle actions.
- `[x]` Module 5 - Admin Lifecycle Actions
  - `[x]` Implemented `POST /v2/admin/actions`.
  - `[x]` Supports add, update permissions, update name, and remove.
  - `[x]` Enforces root/manage-admin rules.
  - `[x]` Enforces at-least-one-root invariant.
  - `[x]` Returns admin action audit entry.
- `[x]` Module 6 - Convert Admin Annotation Auth
  - `[x]` Removed bearer-token dependency from annotation updates.
  - `[x]` Requires `annotate_assets` or `root` for annotation updates.
  - `[x]` Derives admin identity from signing key.
  - `[x]` Keeps asset annotation actions in `actions`.
- `[x]` Module 7 - Forced Delisting Permission
  - `[x]` Added signed asset-scoped admin operations `force_delist_asset` and `force_relist_asset`.
  - `[x]` Requires `delist_assets` or `root`.
  - `[x]` Keeps forced delist/relist audit entries asset-scoped in `actions`.
- `[x]` Module 8 - No-Op Rejection
  - `[x]` Added no-op checks to issuer actions.
  - `[x]` Added no-op checks to admin annotation, forced delist/relist, and admin lifecycle actions.
  - `[x]` Added live PostgreSQL coverage for rejected no-op writes.
- `[x]` Module 9 - Global Audit Union
  - `[x]` Includes `admin_actions` in `GET /v2/audit`.
  - `[x]` Keeps asset-specific audit scoped to `actions`.
  - `[x]` Supports filtering global audit by admin lifecycle operations.
- `[x]` Module 10 - Spec and Docs
  - `[x]` Updated the authored OpenAPI specification with signed admin auth, forced delist/relist, no-op behavior, and global audit union.
  - `[x]` Documented permission model and genesis bootstrap.
  - `[x]` Updated deployment/config docs.
  - `[x]` Updated tests for OpenAPI alignment and admin lifecycle/audit behavior.

## Purpose

Replace the placeholder shared admin bearer token with signed admin actions, explicit admin permissions, a bootstrap genesis admin key, and one registry-assigned audit order across asset and admin-management events.

## Design Decisions

- Admin identity is a secp256k1 public key, not a caller-supplied string.
- Admin requests are signed canonical JSON actions.
- A configured genesis public key bootstraps the first `root` admin when no admins exist.
- The genesis key starts with root power but does not need to remain valid forever.
- `root` implies all permissions.
- Non-root permissions are disjoint at first.
- There must always be at least one active root admin.
- Admin-management events use a new `admin_actions` table.
- Asset-scoped admin actions, such as annotations and forced delisting, remain asset events in `actions`.
- `audit_sequence` remains the authoritative registry event order.
- `server_received_at` remains an observed timestamp, not an ordering primitive.
- Signer timestamps are freshness and anti-stale-write guards, not sequencing.
- Nonces remain in signed payloads for replay and idempotency handling.
- No-op actions are rejected before action insertion.

## Permissions

Initial permissions:

- `root`: implicitly grants every permission.
- `annotate_assets`: update admin annotations such as asset type, featured, malicious, and notes.
- `delist_assets`: forcibly delist or relist assets.
- `review_icons`: approve or reject issuer-submitted icons.
- `manage_admins`: add, remove, or update non-root admins.

Root-specific rules:

- Only `root` can grant `root`.
- Only `root` can revoke `root`.
- Removing or demoting a root admin is rejected if it would leave zero active root admins.
- `manage_admins` cannot create, promote, demote, or remove root admins unless the signer is also root.

## Signed Admin Action Shape

All admin-management actions should use:

```json
{
  "signing_context": "liquid-asset-registry-admin-action-v1",
  "operation": "add_admin",
  "timestamp": "2026-04-30T12:00:00Z",
  "nonce": "9b2b0b3f-8f2e-4f3d-a979-1f2e8a947f87e",
  "admin_pubkey": "02...",
  "friendly_name": "Alice",
  "permissions": ["annotate_assets", "review_icons"]
}
```

Transport:

- Request body is the exact canonical JSON action.
- Signature header: `Asset-Registry-Admin-Signature`.
- Server derives the actor from the verifying public key.
- Caller-supplied admin IDs are not trusted.

Validation:

- Body must already be canonical JSON.
- `timestamp` must be within the accepted freshness window.
- `timestamp` must be greater than or equal to the latest accepted signer timestamp for the signing admin key.
- `nonce` must be unique for the signing admin key.
- Same nonce and same canonical action returns idempotent retry.
- Same nonce and different canonical action is rejected.

## Global Audit Ordering

Use one PostgreSQL sequence across both asset and admin-management actions:

```sql
create sequence audit_sequence_global;
```

Then:

- `actions.audit_sequence default nextval('audit_sequence_global')`
- `admin_actions.audit_sequence default nextval('audit_sequence_global')`

This gives one total registry order across committed events. PostgreSQL sequence allocation is sufficient for ordering; gaps after rollbacks are acceptable and should not be treated as integrity failures.

Global audit reads should `UNION ALL` asset actions and admin actions, then order by `audit_sequence`.

## Database Changes

Add `admin_keys`:

- `admin_uuid uuid primary key`
- `pubkey text unique not null`
- `friendly_name text not null`
- `status text not null`
- `created_by_admin_action_uuid uuid null`
- `removed_by_admin_action_uuid uuid null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Add `admin_permissions`:

- `admin_permission_uuid uuid primary key`
- `admin_uuid uuid not null references admin_keys(admin_uuid)`
- `permission text not null`
- unique `(admin_uuid, permission)`

Add `admin_actions`:

- `admin_action_uuid uuid primary key`
- `audit_sequence bigint unique not null default nextval('audit_sequence_global')`
- `actor_admin_uuid uuid not null references admin_keys(admin_uuid)`
- `actor_pubkey text not null`
- `operation text not null`
- `action jsonb not null`
- `signature text not null`
- `nonce text not null`
- `admin_timestamp timestamptz not null`
- `server_received_at timestamptz not null`
- `created_at timestamptz not null`

Adjust `actions.audit_sequence` to use `audit_sequence_global`.

Indexes:

- `admin_keys_pubkey_uidx`
- `admin_keys_status_idx`
- `admin_permissions_permission_idx`
- `admin_actions_sequence_idx`
- `admin_actions_actor_nonce_uidx`
- `admin_actions_operation_sequence_idx`
- `admin_actions_received_at_idx`

## Admin Operations

Initial admin-management operations:

- `add_admin`
- `update_admin_permissions`
- `update_admin_name`
- `remove_admin`

Optional later operation:

- `rotate_admin_pubkey`

## No-Op Rejection

Reject no-op actions with:

```json
{
  "error": "no_op_action",
  "message": "action would not change registry state"
}
```

Reject before action insertion, so rejected no-ops do not consume a nonce.

Issuer/action no-op cases:

- Replacing category tags with the current normalized list.
- Replacing trading venues with the current normalized list.
- Replacing an existing custom key with a JSON-equal value.
- Deleting a custom key that does not exist.
- Deregistering an already deregistered asset.
- Rotating issuer key to the current issuer key.

Admin no-op cases:

- Updating annotations where all submitted fields equal current values.
- Adding an already-active admin with the same effective friendly name and permissions.
- Removing an already removed or nonexistent admin.
- Updating permissions or friendly name to the current effective values.
- Approving or rejecting an icon already in the requested review state.

## Implementation Modules

### Module 1 - Shared Audit Sequence

- Create `audit_sequence_global`.
- Migrate `actions.audit_sequence` default to the global sequence.
- Preserve existing audit IDs.
- Add regression tests for monotonic action audit IDs.

### Module 2 - Admin Tables

- Add `admin_keys`, `admin_permissions`, and `admin_actions`.
- Add SQLAlchemy models.
- Add migration constraint/index tests.

### Module 3 - Genesis Admin Bootstrap

- Add `ASSET_REGISTRY_GENESIS_ADMIN_PUBKEY`.
- Seed a root admin when no admins exist.
- Do not recreate genesis admin if any admin exists.
- Validate genesis pubkey format with libwally.

### Module 4 - Signed Admin Action Pipeline

- Add canonical admin action schemas.
- Verify `Asset-Registry-Admin-Signature`.
- Enforce timestamp freshness and per-admin timestamp monotonicity.
- Enforce nonce idempotency/conflict behavior.
- Insert `admin_actions`.

### Module 5 - Admin Lifecycle Actions

- Implement `POST /v2/admin/actions`.
- Support add, update permissions, update name, and remove.
- Enforce root/manage-admin rules.
- Enforce at-least-one-root invariant.
- Return admin action audit entry.

### Module 6 - Convert Admin Annotation Auth

- Remove shared bearer-token admin auth.
- Require `annotate_assets` or `root` for annotation updates.
- Derive admin ID from signing key.
- Keep asset annotation actions in `actions`.

### Module 7 - Forced Delisting Permission

- Add explicit forced delist/relist operation.
- Require `delist_assets` or `root`.
- Keep delist/relist audit entries asset-scoped.

### Module 8 - No-Op Rejection

- Add no-op checks to issuer actions.
- Add no-op checks to admin annotation and admin lifecycle actions.
- Add live PostgreSQL coverage for rejected no-op writes.

### Module 9 - Global Audit Union

- Include `admin_actions` in `GET /v2/audit`.
- Keep asset-specific audit scoped to `actions`.
- Add filters for admin lifecycle operations.

### Module 10 - Spec and Docs

- Update the authored OpenAPI specification with signed admin auth.
- Document permission model and genesis bootstrap.
- Update deployment config docs.
- Update CI tests for migrations and admin lifecycle.

## Open Questions

- Should `review_icons` wait until icon storage/review tables exist, or should permission be added now as unused policy vocabulary?
- Should root be required for `remove_admin`, or should `manage_admins` remove non-root admins?
- Should admin-management nonce uniqueness be per admin key or global? The recommended initial scope is per admin key.
- Should rejected admin actions ever be recorded in a separate rejected-action log? The recommended initial behavior is no.
