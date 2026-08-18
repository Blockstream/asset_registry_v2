# Liquid Asset Registry Architecture

This repository implements a FastAPI and PostgreSQL service for registering and governing Liquid network asset metadata. It preserves the established v1 HTTP surface while introducing a v2 action-oriented registry model.

## Goals

- Preserve compatibility for existing registry consumers during migration.
- Keep immutable contract metadata separate from issuer-controlled mutable metadata.
- Make every accepted state change auditable as an append-only action.
- Support issuer key rotation without changing an asset's blockchain identity.
- Replace shared admin credentials with signed, permission-checked governance actions.
- Keep the API contract useful for SDK generation and automated conformance testing.

The service is a registry and governance system. It does not change Liquid issuance transactions, provide an independent domain-proof witness, or make registry annotations part of the on-chain asset contract.

## API and Versioning Model

Legacy operations remain at the root paths for v1-compatible registration, listing, lookup, and deregistration. New operations live under `/v2` and use normalized response models, pagination, signed actions, and audit endpoints.

`contract.version` identifies the immutable contract metadata format. `mutable_schema_version` identifies the mutable action format. These versions evolve independently.

FastAPI and Pydantic are the source of truth for the OpenAPI contract. The tracked `openapi.yaml` is generated from the application for SDKs, reviews, and releases; it must not be edited manually.

## Asset Identity and Metadata

The blockchain `asset_id` is the stable external identity. Database rows use UUID primary keys so application identity is independent of blockchain identifiers and historical records.

Contract metadata is immutable after registration. For v2 and later contracts, `initial_issuer_pubkey` is included in the contract and therefore in its contract hash. `current_issuer_pubkey` is registry state initialized from that key and changed only through an accepted rotation action.

Issuer-controlled mutable metadata is normalized into category tags, trading venues, and custom attributes. Admin annotations—such as featured, malicious, delisted, and asset type—are stored separately and cannot rewrite issuer metadata.

## Signed Actions and Canonical JSON

Issuer and admin mutations submit an action object as the HTTP request body. The signature covers the canonical JSON bytes of that object; it does not cover URL or header data.

Canonical JSON uses recursively sorted object keys, no insignificant whitespace, deterministic scalar encoding, UTF-8, and no non-finite numbers. Signed endpoints reject non-canonical bodies so stored payload bytes, verified bytes, and audit representations do not diverge.

Issuer actions use the `Asset-Registry-Signature` header and the asset's current issuer key. Admin actions use `Asset-Registry-Admin-Signature`, declare `actor_pubkey` in the signed body, and require that key to be an active admin with the necessary permission.

Icon uploads use an envelope containing a signed `action` and an unsigned Base64 `icon`. The action commits to the decoded PNG bytes through `icon_hash`; the server canonicalizes and verifies only the nested action, then independently decodes, validates, and hashes the image. Issuer uploads create pending proposals. Admins with `manage_icons` can use the same hash-committed transport to publish an icon immediately. Pending-review searches use a separate signed admin-query context and do not create audit events. Issuer proposal searches use their own signed query context and return only proposals submitted by that signing key.

## Replay Protection and Issuer Action Chain

Signed actions carry a timestamp and nonce. Nonces are scoped to the relevant asset or admin identity. Repeating the same nonce with the exact same accepted action is an idempotent retry; reusing it for different content is a conflict.

Issuer actions also include `prev_action_hash`. It must match the latest accepted issuer-chain action hash for the asset, allowing clients to detect omitted or reordered issuer mutations. Admin actions do not participate in the issuer hash chain.

Freshness checks limit timestamp drift and prevent a signer from submitting an action older than its latest accepted action. No-op actions are rejected before they consume a nonce or audit sequence.

## Admin Governance

Admin keys and permissions are managed through signed lifecycle actions. Permissions cover admin management, annotations, delisting, icon review, and legacy migration; `root` implies all permissions.

If no admins exist, a configured genesis public key can bootstrap the first root administrator. After bootstrap, all lifecycle and asset-scoped changes follow the normal signed-action and audit paths.

Icon proposal review status (`pending`, `rejected`, or `approved`) is independent from registration obsolescence. `assets.active_icon_proposal_uuid` explicitly selects the published icon; replacement moves that pointer while preserving earlier approved bytes. A direct admin upload creates an approved `admin_upload` proposal, approves an identical pending issuer proposal in place, or reuses identical historical approved bytes. Deregistration clears the pointer and timestamps all associated proposals as obsolete without changing their review status. Approval, rejection, and direct assignment are asset-scoped admin audit actions, while issuer proposals participate in the issuer action hash chain.

## Audit Model

Accepted state changes are append-only. Asset and admin lifecycle actions share a global PostgreSQL audit sequence so the global audit endpoint can produce one deterministic order.

Audit entries keep registry-observed metadata—such as `audit_id`, server receive time, actor identity, and verified key—outside the signed action. The original action and its signature remain available for verification. Asset audit projects asset-scoped actions; global audit merges asset and admin lifecycle actions.

## Chain and Domain Verification

Registration can verify the issuance commitment through an Esplora-compatible Liquid API. It can also verify issuer domain control through HTTP or DNS proofs. V2 supports a pubkey-bound domain proof whose key must match the resolved initial issuer key.

Domain verification is an acceptance-time registry check, not a durable independent witness. Audit entries show that the registry accepted the proof but do not prove what a domain served at that historical moment. Stronger guarantees would require DNSSEC, transparency infrastructure, zkTLS, or a separate notary system.

External verification failures are kept distinct from invalid submitted proofs so operators and clients can distinguish dependency outages from rejected registrations.

## Legacy Compatibility

Legacy contract fields that are not first-class v2 columns are retained in `contract_extra_fields`, allowing migrated records to reconstruct their original contract without allowing arbitrary fields in native v2 contracts.

During migration, supported legacy writes can be shadow-forwarded to the original registry after local persistence. Shadow outcomes are logged, and the compatibility layer preserves legacy status behavior where practical. A v1-to-v2 migration changes the registry model only; it does not recreate or alter the chain issuance.

## Persistence and Transactions

SQLAlchemy models use UUID keys, explicit constraints, and normalized child tables. Service-layer writes are transactional and roll back on failure. Serialized fragments are cached for large compatibility listings and refreshed with the underlying asset state.

See [`schema.md`](schema.md) for the database design, [`docs/configuration.md`](docs/configuration.md) for runtime settings, and [`docs/deployment.md`](docs/deployment.md) for operational guidance.
