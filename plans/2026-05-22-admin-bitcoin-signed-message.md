# Admin Bitcoin Signed Message Plan

Date: 2026-05-22

## Implementation Status

Legend:

- `[x]` completed and verified locally where possible
- `[~]` implemented or partially verified, with remaining integration work
- `[ ]` not started

Current progress:

- `[x]` Module 1 - Finalize Admin Signing Contract
- `[x]` Module 2 - API Schema and Verification
- `[x]` Module 3 - Admin Action Storage and Audit
- `[x]` Module 4 - SDK Signing
- `[x]` Module 5 - Tests
- `[x]` Module 6 - OpenAPI and Documentation

## Purpose

Convert admin action signatures from recoverable secp256k1 signatures to the same Bitcoin Signed Message style compact signatures used by issuer actions.

This keeps the SDK and API signing model easier to explain:

- Issuer actions sign canonical JSON using Bitcoin Signed Message formatting.
- Admin actions sign canonical JSON using Bitcoin Signed Message formatting.
- Domain proof registration signatures continue to use the existing issuer-style signature behavior.

## Design Decision

Require admin action payloads to include the signing admin public key explicitly as `actor_pubkey`.

Rationale:

- A compact Bitcoin Signed Message signature does not include a recovery ID.
- Without recovery, the server needs a declared public key to verify against.
- Putting `actor_pubkey` in the canonical JSON payload makes the claimed actor part of what is signed.
- `actor_pubkey` avoids overloading existing fields such as `admin_pubkey`, which identifies the target admin for lifecycle actions.

Example lifecycle action:

```json
{
  "signing_context": "liquid-asset-registry-admin-action-v1",
  "actor_pubkey": "02...",
  "operation": "add_admin",
  "timestamp": "2026-05-22T12:00:00Z",
  "nonce": "uuid",
  "admin_pubkey": "03...",
  "friendly_name": "Ops",
  "permissions": ["annotate_assets"]
}
```

The signature header remains:

```http
Asset-Registry-Admin-Signature: <base64 compact signature>
```

## Compatibility Decision

Prefer a direct development change unless there is already a deployed client that needs a transition window.

Direct change:

- Require `actor_pubkey` on all admin lifecycle and asset-scoped actions.
- Reject recoverable-signature-only admin requests.
- Remove the admin recoverable-signature path from the SDK.
- Keep existing database rows valid because stored audit rows already include `actor_pubkey`.

Optional transition path:

- Temporarily accept both formats:
  - New compact Bitcoin Signed Message signature with `actor_pubkey`.
  - Old 65-byte recoverable signature without `actor_pubkey`.
- Return or log a deprecation warning for recoverable admin signatures.
- Remove the fallback before public SDK/API stabilization.

The direct path is simpler and fits the current development stage.

## Verification Model

For admin actions:

1. Parse the canonical JSON payload.
2. Validate `actor_pubkey` as a compressed secp256k1 public key.
3. Verify `Asset-Registry-Admin-Signature` against the exact canonical JSON payload bytes using Bitcoin Signed Message formatting.
4. Look up `actor_pubkey` in `admin_keys`.
5. Require the admin key to be active.
6. Apply existing freshness, nonce, and permission checks unchanged.

Important detail:

- The signature is over the payload including `actor_pubkey`.
- The server must not inject or rewrite `actor_pubkey` before verification.
- The canonical JSON requirement remains unchanged.

## Implementation Modules

### Module 1 - Finalize Admin Signing Contract - Completed

- `[x]` Confirm field name: `actor_pubkey`.
- `[x]` Confirm `actor_pubkey` is required for every admin lifecycle action.
- `[x]` Confirm `actor_pubkey` is required for every asset-scoped admin action.
- `[x]` Confirm admin signatures use the same Bitcoin Signed Message verifier as issuer actions.
- `[x]` Decide whether to keep a temporary recoverable-signature fallback. No fallback was kept.

### Module 2 - API Schema and Verification - Completed

- `[x]` Add `actor_pubkey` to the shared admin action schema base.
- `[x]` Validate `actor_pubkey` with the existing pubkey validators.
- `[x]` Replace `recover_canonical_payload_signer_pubkey()` usage in admin verification with `verify_canonical_payload_signature(actor_pubkey, signature, payload)`.
- `[x]` Preserve current error code behavior where possible:
  - invalid base64 or bad signature returns `invalid_signature`.
  - inactive or unknown actor key returns `forbidden`.
  - missing or malformed `actor_pubkey` returns `validation_error` or `invalid_pubkey`.
- `[x]` Keep freshness and nonce checks unchanged.

### Module 3 - Admin Action Storage and Audit - Completed

- `[x]` Continue storing `actor_pubkey` in `admin_actions.actor_pubkey`.
- `[x]` Continue storing `actor_pubkey` in asset-scoped admin action rows through `actions.verified_pubkey`.
- `[x]` Ensure audit responses still expose the verified actor pubkey.
- `[x]` Do not add a migration; this is a payload/schema change only.

### Module 4 - SDK Signing - Completed

- `[x]` Revert admin client calls from `signRecoverableData()` back to issuer-style `signData()`.
- `[x]` Have `AdminClient` add `actor_pubkey` when constructing helper actions.
- `[x]` Add `getPubkey()` to the built-in `Signer`.
- `[x]` Allow callers to pass `actorPubkey` into `AdminClient` options for custom or remote signers.
- `[x]` Update static admin signing helpers to require `actor_pubkey` in the action object before signing.
- `[x]` Remove recoverable admin signing helpers.

### Module 5 - Tests - Completed

- `[x]` API tests prove admin lifecycle actions verify with compact Bitcoin Signed Message signatures.
- `[x]` API tests reject missing `actor_pubkey`.
- `[x]` API tests reject mismatched `actor_pubkey` and signature.
- `[x]` API tests keep permission failures distinct from signature failures.
- `[x]` SDK tests prove `AdminClient` sends `actor_pubkey` in signed payloads.
- `[x]` SDK tests prove admin signatures use `signData()` rather than `signRecoverableData()`.

### Module 6 - OpenAPI and Documentation - Completed

- `[x]` Update FastAPI/OpenAPI schemas to show `actor_pubkey` on admin action payloads.
- `[x]` Update SDK examples.
- `[x]` Update `AGENTS.md` admin signing description.
- `[x]` Note that `Asset-Registry-Admin-Signature` is now a compact Bitcoin Signed Message signature over canonical JSON.

## Open Questions

- Should `actor_pubkey` be supplied to `AdminClient` explicitly, or should the built-in signer expose its public key?
- Should remote signers be expected to expose a public key endpoint, or should callers pass the public key separately?
- Should the API accept the old recoverable signature format for a short local transition period, or is a direct break acceptable before public release?
