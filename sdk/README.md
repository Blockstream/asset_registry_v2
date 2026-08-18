# Liquid Asset Registry SDK

TypeScript client for the Liquid Asset Registry v2 and legacy-compatible APIs.

## Installation

```bash
npm install liquid-asset-registry-sdk
```

Node.js 18.18+ or a modern browser with `fetch`, `AbortController`, `atob`, and `btoa` is required.

## Read and Search Assets

```typescript
import { AssetRegistryClient } from "liquid-asset-registry-sdk";

const registry = new AssetRegistryClient({
  baseUrl: "https://registry.example.com",
  timeout: 30_000,
  maxRetries: 3,
});

const page = await registry.v2.search({
  page: 1,
  pageSize: 50,
  sort: "updated_at_desc",
  categoryTags: ["stablecoin", "tokenized"],
  tradingVenue: "bitfinex",
});

console.log(page.items, page.total_count, page.total_pages);

const asset = await registry.v2.get("a".repeat(64));
const assetsById = await registry.v2.getAll();

if (asset?.icon) {
  const iconUrl = new URL(asset.icon.href, "https://registry.example.com");
  console.log(iconUrl.toString());
}
```

`getAll()` returns an object keyed by asset ID, matching `/v2/assets/all.json`.
Every asset response contains `icon`, either as a registry-relative,
content-addressed `href` or `null`. Hashed icon URLs are immutable and can be
used directly as image sources after resolving them against the registry base
URL. Call `registry.legacy.getIcons()` when one bulk Base64 map is preferable.

## Register an Asset

```typescript
const registration = {
  asset_id: "07c22bef610db8776bf377f885acc13711eede0f918f33e480b94be3ff40513f",
  contract: {
    entity: { domain: "example.com" },
    initial_issuer_pubkey: "027c8ac4997d39582bca97bed1015385c15e237054e0c9606125be8c9b9cc1a506",
    name: "Example",
    precision: 0,
    ticker: "EXAMPLE",
    version: 2,
  },
  domain_verification_method: "dns" as const,
};

await registry.v2.register(registration);
```

For a pubkey-bound domain proof, configure a signer and sign the normalized contract:

```typescript
import { AssetRegistryClient, Signer } from "liquid-asset-registry-sdk";

const signer = new Signer(privateKey);
const signingRegistry = new AssetRegistryClient({
  baseUrl: "https://registry.example.com",
  signer,
});

const { signature } = await signingRegistry.v2.signRegistrationContract(registration);
await signingRegistry.v2.register(registration, { domainSignature: signature });
```

## Issuer Actions

Supply either a custom signer or an issuer private key. These options are mutually exclusive.

```typescript
const issuerRegistry = new AssetRegistryClient({
  baseUrl: "https://registry.example.com",
  issuerPrivateKey,
});

await issuerRegistry.v2.replaceCategoryTags(assetId, ["stablecoin"]);
await issuerRegistry.v2.replaceTradingVenues(assetId, [
  { venue: "bitfinex", url: "https://example.com/market" },
]);
await issuerRegistry.v2.setCustomField(assetId, "isin", "US0000000000");
await issuerRegistry.v2.deleteCustomField(assetId, "isin");
await issuerRegistry.v2.rotateIssuerKey(assetId, newIssuerPubkey);
const pngBytes = new Uint8Array(await uploadedFile.arrayBuffer());
await issuerRegistry.v2.proposeIcon(assetId, pngBytes);
await issuerRegistry.v2.deregisterAsset(assetId);
```

Issuer helpers fetch `prev_action_hash`, fill the nonce and timestamp, sign one canonical body, and reuse that exact body for any safe retry.

## Admin Actions and Migration

```typescript
import { AdminClient, Signer } from "liquid-asset-registry-sdk";

const admin = new AdminClient({
  baseUrl: "https://registry.example.com",
  signer: new Signer(adminPrivateKey),
});

await admin.addAdmin(newAdminPubkey, ["manage_admins"], "Operations");
await admin.updateAnnotations(assetId, {
  asset_type: "stablecoin",
  featured: true,
  admin_notes: "Reviewed",
});
await admin.forceDelistAsset(assetId, "Policy review");
await admin.forceRelistAsset(assetId, "Review resolved");
const pendingIcons = await admin.listPendingIconProposals({ order: "asc" });
await admin.approveIcon(assetId, pendingIcons.items[0].icon_hash);
// Or: await admin.rejectIcon(assetId, pendingIcons.items[0].icon_hash, "Needs transparent background");
await admin.setIcon(assetId, new Uint8Array(await adminUploadedFile.arrayBuffer()));
await admin.migrateAsset(legacyAssetId);
```

A custom admin signer without `getPubkey()` must also provide `actorPubkey` in the configuration.

Issuers can retrieve only proposals made by their signing key:

```typescript
const proposals = await registry.v2.listIconProposals(assetId, {
  status: "approved",
  order: "desc",
});
```

## Audit Logs

Audit pagination uses an append-only cursor rather than page numbers.

```typescript
const audit = await registry.v2.getAssetAudit(assetId, {
  limit: 100,
  sinceAuditId: 0,
  order: "asc",
});

const globalAudit = await registry.v2.searchAudit({
  assetId,
  operation: "deregister",
  actor: "issuer",
  fromServerReceivedAt: "2026-01-01T00:00:00Z",
  order: "desc",
});

console.log(audit.items, audit.next_since_audit_id, globalAudit.items);
```

Issuer key history is embedded in each asset response. `getIssuerPubkeyHistory(assetId)` provides a convenience lookup.

## Legacy API

```typescript
const assetsById = await registry.legacy.list();
const iconsById = await registry.legacy.getIcons();
const asset = await registry.legacy.get(assetId);
await registry.legacy.deregister(assetId, legacyDeletionSignature);
```

Legacy deletion signatures cover the message `remove <asset_id> from registry` in Bitcoin Signed Message format.

## Errors and Signing Utilities

HTTP failures throw `HttpError`, which exposes `statusCode`, the registry error `code`, structured `details`, and a non-enumerable raw `body`.

The root package also exports:

- `canonicalJson(value)`
- `signData(data, { privateKey })`
- `verifySignature(data, { signature, pubkey })`
- `generateKeyPair()`
- `Signer` and `SignerBase`

## Development

```bash
npm ci                  # install the exact dependency versions from package-lock.json
npm run audit           # fail on high- or critical-severity advisories
npm run generate:types  # regenerate from ../openapi.yaml
npm run check:types     # fail if generated types are stale
npm run check           # audit, format check, lint, typecheck, and tests
npm run build           # declarations, ESM, and CommonJS
```

The OpenAPI snapshot is the source of truth for wire request and response types. Generated declarations in `src/generated/` should not be edited manually.
