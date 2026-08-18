# Pubkey-Bound Domain Proof Plan

Date: 2026-05-14

## Implementation Status

Legend:

- `[x]` completed and verified locally where possible
- `[~]` implemented or partially verified, with remaining integration work
- `[ ]` not started

Current progress:

- `[x]` Module 1 - Finalize v2 Proof Contract
- `[x]` Module 2 - Proof Parser and Validation
- `[x]` Module 3 - Signature Payload Definition
- `[x]` Module 4 - v2 Registration Integration
- `[x]` Module 5 - Error Diagnostics
- `[x]` Module 6 - Tests
- `[x]` Module 7 - OpenAPI and Documentation
- `[x]` Module 8 - SDK Follow-Up

## Purpose

Add a v2-only pubkey-bound domain proof path for issuers that want to prove control of a domain once for a public key, then register multiple assets from that domain using signatures from the same key.

This does not remove or replace the existing asset-bound HTTP/DNS domain proof methods. Those remain available for users who prefer per-asset domain proofs or do not want a reusable pubkey-bound proof.

## Design Decision

Implement this feature only for the v2 API.

Rationale:

- The request came from a user targeting the v2 registry.
- The legacy registry/API should remain stable while it is being phased out over the next couple of years.
- Keeping legacy validation unchanged reduces code paths, compatibility risk, and OpenAPI churn.
- v2 already has clearer issuer-key semantics through `initial_issuer_pubkey` / `current_issuer_pubkey`.

## Proof Model

The domain proof delegates registry domain authority to a compressed secp256k1 public key.

Proof content format for both DNS TXT and HTTP proof files:

```text
context=liquid-asset-registry-v2,pubkey=02...
```

Rules:

- The proof is comma-separated key/value content.
- Required keys are `context` and `pubkey`.
- `context` must equal `liquid-asset-registry-v2`.
- `pubkey` must be a valid compressed secp256k1 public key.
- Whitespace around comma-separated pairs may be tolerated.
- Duplicate keys must be rejected.
- Unknown keys should be rejected for this first version unless a future extension strategy is explicitly added.
- No network field is included. Domain ownership is not scoped to `liquid` or `liquidtestnet`; the same domain owner controls the domain for both.

## Signature Model

For pubkey-bound domain proof, the v2 registration request must include `Asset-Registry-Signature`.

The signature covers the normalized contract only, not the full registration request body.

Recommended normalized contract payload:

```python
request.contract.model_dump(mode="json", exclude_none=True)
```

Then canonical JSON encode that normalized object using the registry canonical JSON rules, and verify the header signature against those canonical bytes.

The proof pubkey must match the resolved initial issuer pubkey:

- For v2+ contracts, match `contract.initial_issuer_pubkey`.
- For older contract versions accepted through v2 registration, match the resolved body-level `initial_issuer_pubkey`.

## Existing Proof Compatibility

Keep current asset-bound domain verification behavior intact.

The v2 registration domain verification flow should accept either:

- Existing asset-bound HTTP/DNS proof format.
- New pubkey-bound HTTP/DNS proof format plus valid `Asset-Registry-Signature` over the normalized contract.

If both proof styles are present, prefer the pubkey-bound proof only when it parses cleanly and the signature verifies. Otherwise, fall back only when doing so cannot hide a malformed pubkey-bound proof in a surprising way. This fallback rule should be made explicit during implementation.

## Implementation Modules

### Module 1 - Finalize v2 Proof Contract - Completed

- Confirm public name: pubkey-bound domain proof.
- Confirm proof context string: `liquid-asset-registry-v2`.
- Confirm proof content format: `context=liquid-asset-registry-v2,pubkey=<pubkey>`.
- Confirm no network field.
- Confirm feature is v2-only and legacy registration behavior is unchanged.
- Decide whether malformed pubkey-bound proofs should fail immediately or allow fallback to existing asset-bound proofs.

### Module 2 - Proof Parser and Validation - Completed

- Add a parser for comma-separated key/value proof content.
- Trim whitespace around fields.
- Require exactly `context` and `pubkey`.
- Reject duplicate keys.
- Reject unknown keys for this version.
- Validate context value.
- Normalize and validate pubkey with existing pubkey validation helpers.
- Add focused unit tests for parser edge cases.

### Module 3 - Signature Payload Definition - Completed

- Add helper to produce canonical normalized contract bytes.
- Use `request.contract.model_dump(mode="json", exclude_none=True)` as payload source.
- Verify `Asset-Registry-Signature` against the pubkey-bound proof pubkey.
- Confirm signature format matches existing issuer action signature verification.
- Keep signature verification independent of mutable metadata and wrapper request fields.

### Module 4 - v2 Registration Integration - Completed

- Accept optional `Asset-Registry-Signature` header on `POST /v2/assets`.
- Pass the raw header into `register_v2_asset`.
- Extend v2 domain verification to check for pubkey-bound proof content.
- Require the proof pubkey to match the resolved initial issuer pubkey.
- Preserve current asset-bound HTTP/DNS verification as a valid path.
- Do not change legacy root registration.

### Module 5 - Error Diagnostics - Completed

- Return a clear error when pubkey-bound proof is present but the signature header is missing.
- Return a clear error when proof pubkey does not match the registration issuer pubkey.
- If verification over normalized contract fails, optionally try verifying against the canonical registration request body only for diagnostics.
- If request-body verification succeeds, return a message such as: `domain proof signature must cover the normalized contract JSON, not the registration request body`.
- Include structured details such as `expected_payload: normalized_contract`.
- Avoid leaking anything beyond user-submitted public metadata and hashes.

### Module 6 - Tests - Completed

- Parser accepts valid comma-separated proof content.
- Parser rejects missing, duplicate, or unknown fields.
- HTTP pubkey-bound proof accepts valid v2 registration with matching signature.
- DNS pubkey-bound proof accepts valid v2 registration with matching signature.
- Existing asset-bound HTTP/DNS proof tests continue to pass.
- Missing signature is rejected for pubkey-bound proof.
- Signature over full request body returns the specific diagnostic error.
- Mismatched proof pubkey is rejected.
- Legacy registration remains unchanged.

### Module 7 - OpenAPI and Documentation - Completed

- Document pubkey-bound proof content format in the v2 OpenAPI contract.
- Document that `Asset-Registry-Signature` is optional for v2 registration, but required when using pubkey-bound domain proof.
- Document that the signature covers normalized contract JSON.
- Document that legacy domain proof behavior is unchanged.
- Add examples for HTTP and DNS pubkey-bound proofs.

### Module 8 - SDK Follow-Up - Completed

- Add helper to canonicalize normalized contract payload for signing if needed.
- Add support for passing `Asset-Registry-Signature` during v2 registration.
- Add documentation/example for pubkey-bound domain proof registration.
- Add tests that SDK registration signs the normalized contract rather than the full registration request.

## Open Questions

- Should unknown proof keys be rejected now or ignored for forward compatibility? Recommended: reject now.
- Should a malformed pubkey-bound proof prevent fallback to an asset-bound proof? Recommended: fail explicitly if the proof appears to target `liquid-asset-registry-v2`.
- Should the diagnostic response include the canonical normalized contract string or only its hash? Recommended: include the hash by default; include the canonical string only if it matches existing error-detail practices.
