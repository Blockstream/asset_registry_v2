# Client SDK Plan

Date: 2026-05-05

## Implementation Status

Legend:

- `[x]` completed and verified locally where possible
- `[~]` implemented or partially verified, with remaining integration work
- `[ ]` not started

Current progress:

- `[ ]` Module 1 - SDK Scaffold and Dependencies
- `[ ]` Module 2 - HTTP Client and Configuration
- `[ ]` Module 3 - Asset Registration
- `[ ]` Module 4 - Asset Search and Lookup
- `[ ]` Module 5 - Issuer Actions
- `[ ]` Module 6 - Admin Governance
- `[ ]` Module 7 - Audit and History
- `[ ]` Module 8 - Signature Utilities
- `[ ]` Module 9 - Error Handling
- `[ ]` Module 10 - TypeScript Type Definitions
- `[ ]` Module 11 - Documentation and Examples
- `[ ]` Module 12 - Testing Suite

## Purpose

Build a client SDK for the Liquid Asset Registry v2 API that provides:

- **TypeScript/JavaScript** - For browser-based and Node.js clients
- **Python** - For backend integration and CLI tools

The SDK abstracts:
- HTTP client setup and configuration
- Canonical JSON serialization for signing
- Signature verification and creation
- Request/response typing and validation
- Pagination handling
- Error interpretation and retry logic

## Design Goals

### API Style

**Object-oriented for convenience, functions for composition**:

```typescript
// OO style for simple operations
const registry = new AssetRegistryClient({ baseUrl, issuerKeyPair });
const asset = await registry.getAsset(assetId);

// Functional style for advanced use cases
const response = await searchAssets({
  client: registry,
  filters: { categories: ["stablecoin"] },
});
```

### Type Safety

- Full TypeScript type definitions generated from OpenAPI spec
- Runtime validation for critical fields
- Discriminated unions for action types

### Error Handling

- Consistent error types with clear codes
- Automatic retry for transient errors (429, 5xx)
- Detailed error messages with suggestions

### Browser Compatibility

- Web Crypto API for signing (supports browser usage)
- No Node.js-specific APIs in browser build
- ES modules for modern bundlers

## Proposed Architecture

### Directory Structure

```
asset_registry/
├── package.json
├── tsconfig.json
├── tsconfig.esm.json
├── tsconfig.cjs.json
├── README.md
├── src/
│   ├── index.ts
│   ├── client/
│   │   ├── AssetRegistryClient.ts
│   │   ├── LegacyClient.ts
│   │   └── V2Client.ts
│   ├── api/
│   │   ├── registration.ts
│   │   ├── search.ts
│   │   ├── lookup.ts
│   │   ├── issuerActions.ts
│   │   ├── admin.ts
│   │   ├── adminActions.ts
│   │   ├── adminAnnotations.ts
│   │   ├── migration.ts
│   │   └── audit.ts
│   ├── types/
│   │   ├── index.ts
│   │   ├── assets.ts
│   │   ├── actions.ts
│   │   ├── admin.ts
│   │   ├── errors.ts
│   │   └── responses.ts
│   ├── utils/
│   │   ├── canonicalJson.ts
│   │   ├── signatures.ts
│   │   ├── crypto.ts
│   │   ├── pagination.ts
│   │   ├── http.ts
│   │   ├── retry.ts
│   │   └── validation.ts
│   └── errors/
│       ├── RegistryError.ts
│       ├── SignatureError.ts
│       ├── ValidationError.ts
│       └── HttpError.ts
├── __tests__/
│   ├── client/
│   ├── api/
│   ├── utils/
│   └── integration/
└── examples/
    ├── register-asset.ts
    ├── search-assets.ts
    ├── issuer-action.ts
    └── admin-action.ts
```

### Module Descriptions

#### Module 1 - SDK Scaffold and Dependencies

Set up the project structure:

- TypeScript configuration for ESM and CJS outputs
- Build pipeline (rollup or esbuild)
- Testing setup (Jest + ts-jest)
- Type definition generation from OpenAPI spec
- ESLint and Prettier configuration
- `.npmignore` for clean publishes

**Dependencies:**

- `ecdsa-sig-formatter` - Signature parsing
- `tweetnacl` or `@noble/curves/secp256k1` - secp256k1 operations
- `uuid` - Nonce generation
- `fetch` - Polyfill for Node.js (optional)

#### Module 2 - HTTP Client and Configuration

Core client infrastructure:

**AssetRegistryClient**:

```typescript
interface ClientConfig {
  baseUrl: string;                    // Required
  apiKey?: string;                    // Optional, for authenticated endpoints
  issuerPubkey?: string;             // For issuer-signed actions
  issuerPrivateKey?: string | Uint8Array; // For issuer-signed actions
  adminPubkey?: string;              // For admin actions
  adminPrivateKey?: string | Uint8Array;  // For admin actions
  timeout?: number;                  // Request timeout (ms)
  maxRetries?: number;              // Retry attempts
  retryDelay?: number;              // Retry delay (ms)
}

class AssetRegistryClient {
  constructor(config: ClientConfig);
  
  // Legacy client delegation
  readonly legacy: LegacyClient;
  
  // v2 client delegation
  readonly v2: V2Client;
  
  // Shared configuration
  readonly config: ClientConfig;
}
```

**HTTP Layer:**

```typescript
class HttpClient {
  request<T>(options: RequestOptions): Promise<T>;
  get<T>(path: string, options?: RequestOptions): Promise<T>;
  post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T>;
  put<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T>;
  delete<T>(path: string, options?: RequestOptions): Promise<T>;
}
```

#### Module 3 - Asset Registration

Asset registration for both legacy and v2:

```typescript
// Legacy registration (v1)
interface LegacyAsset {
  assetId: string;                  // Hex-encoded asset ID
  name: string;
  ticker: string;
  precision: number;               // Decimal precision
  description?: string;
  issuerPubkey?: string;           // Optional issuer pubkey
}

async function registerAssetLegacy(
  client: AssetRegistryClient,
  asset: LegacyAsset,
  options?: RequestOptions
): Promise<LegacyAssetResponse>;

// v2 registration
interface V2Asset {
  assetId: string;                  // Hex-encoded asset ID
  contractHash: string;             // Hex-encoded contract hash
  contractLeaf: string;             // Hex-encoded leaf hash
  contractVersion: number;          // Contract version
  metadata: {
    name: string;
    ticker: string;
    precision: number;
    description?: string;
    website?: string;
    supportedLanguages?: string[];
    contractContentHash?: string;
    contractContentType?: string;
  };
  issuanceCommitment?: string;      // Optional issuance commitment
  initialIssuerPubkey?: string;     // Optional initial issuer pubkey
  domain?: string;                 // Optional domain for verification
  mutable?: {
    categoryTags?: string[];
    tradingVenues?: TradingVenue[];
    custom?: Record<string, unknown>;
  };
}

async function registerAssetV2(
  client: AssetRegistryClient,
  asset: V2Asset,
  options?: RequestOptions
): Promise<AssetResponse>;

// Convenience methods on client
async function registerAsset(
  client: V2Client,
  asset: V2Asset
): Promise<AssetResponse>;

async function registerAssetLegacy(
  client: LegacyClient,
  asset: LegacyAsset
): Promise<LegacyAssetResponse>;
```

#### Module 4 - Asset Search and Lookup

Search and retrieve assets:

```typescript
// Search assets
interface SearchParams {
  q?: string;                      // Full-text search
  category?: string;              // Filter by category
  tradingVenue?: string;          // Filter by trading venue
  domain?: string;                // Filter by domain
  page?: number;                  // Page number (1-indexed)
  limit?: number;                 // Results per page (max 100)
  sortBy?: "name" | "asset_id" | "created_at";
  sortOrder?: "asc" | "desc";
}

interface SearchResponse {
  assets: Asset[];
  pagination: {
    page: number;
    limit: number;
    total: number;
    totalPages: number;
  };
}

async function searchAssets(
  client: V2Client,
  params: SearchParams
): Promise<SearchResponse>;

// Get all assets as flat JSON (for clients that need full dataset)
async function getAllAssets(client: V2Client): Promise<Asset[]>;

// Lookup by asset ID
async function getAsset(
  client: V2Client,
  assetId: string
): Promise<Asset | null>;

// Legacy lookup
async function getAssetLegacy(
  client: LegacyClient,
  assetId: string
): Promise<LegacyAsset | null>;

// List legacy assets
async function listAssetsLegacy(
  client: LegacyClient
): Promise<LegacyAsset[]>;
```

#### Module 5 - Issuer Actions

Submit signed issuer actions:

```typescript
// Issuer action types
interface IssuerActionBase {
  signingContext: "liquid-asset-registry-action-v1";
  assetId: string;
  operation: string;
  mutableSchemaVersion: number;
  timestamp: string;              // ISO 8601
  nonce: string;                  // UUID
}

// Specific action types
interface ReplaceCategoryTagsAction extends IssuerActionBase {
  operation: "replace_category_tags";
  categoryTags: string[];
}

interface ReplaceTradingVenuesAction extends IssuerActionBase {
  operation: "replace_trading_venues";
  tradingVenues: TradingVenue[];
}

interface ReplaceCustomAction extends IssuerActionBase {
  operation: "replace_custom";
  custom: Record<string, unknown>;
}

interface SetCustomFieldAction extends IssuerActionBase {
  operation: "set_custom_field";
  customKey: string;
  value: unknown;
}

interface DeleteCustomFieldAction extends IssuerActionBase {
  operation: "delete_custom_field";
  customKey: string;
}

interface DeregisterAction extends IssuerActionBase {
  operation: "deregister";
}

interface RotateIssuerPubkeyAction extends IssuerActionBase {
  operation: "rotate_issuer_pubkey";
  newIssuerPubkey: string;
}

type IssuerAction = 
  | ReplaceCategoryTagsAction
  | ReplaceTradingVenuesAction
  | ReplaceCustomAction
  | SetCustomFieldAction
  | DeleteCustomFieldAction
  | DeregisterAction
  | RotateIssuerPubkeyAction;

// Submit issuer action
interface SubmitIssuerActionOptions {
  assetId: string;
  action: IssuerAction;
  issuerPubkey?: string;           // Required if not set in client config
  issuerPrivateKey?: string | Uint8Array; // Required if not set in client config
}

async function submitIssuerAction(
  client: V2Client,
  options: SubmitIssuerActionOptions
): Promise<ActionResponse>;

// Convenience methods
async function replaceCategoryTags(
  client: V2Client,
  assetId: string,
  categoryTags: string[],
  options?: Omit<SubmitIssuerActionOptions, 'assetId' | 'action'>
): Promise<ActionResponse>;

async function replaceTradingVenues(
  client: V2Client,
  assetId: string,
  tradingVenues: TradingVenue[],
  options?: Omit<SubmitIssuerActionOptions, 'assetId' | 'action'>
): Promise<ActionResponse>;

async function setCustomField(
  client: V2Client,
  assetId: string,
  key: string,
  value: unknown,
  options?: Omit<SubmitIssuerActionOptions, 'assetId' | 'action'>
): Promise<ActionResponse>;

async function deregisterAsset(
  client: V2Client,
  assetId: string,
  options?: Omit<SubmitIssuerActionOptions, 'assetId' | 'action'>
): Promise<ActionResponse>;

async function rotateIssuerKey(
  client: V2Client,
  assetId: string,
  newPubkey: string,
  options?: Omit<SubmitIssuerActionOptions, 'assetId' | 'action'>
): Promise<ActionResponse>;
```

#### Module 6 - Admin Governance

Admin lifecycle and asset-scoped actions:

```typescript
// Admin lifecycle action types
interface AdminLifecycleActionBase {
  signingContext: "liquid-registry-admin-v1";
  operation: string;
  adminTimestamp: string;         // ISO 8601
  nonce: string;                  // UUID
}

interface AddAdminAction extends AdminLifecycleActionBase {
  operation: "add_admin";
  adminPubkey: string;
  name?: string;
  permissions: string[];
}

interface UpdateAdminPermissionsAction extends AdminLifecycleActionBase {
  operation: "update_admin_permissions";
  adminUuid: string;
  permissions: string[];
}

interface UpdateAdminNameAction extends AdminLifecycleActionBase {
  operation: "update_admin_name";
  adminUuid: string;
  name: string;
}

interface RemoveAdminAction extends AdminLifecycleActionBase {
  operation: "remove_admin";
  adminUuid: string;
}

type AdminLifecycleAction = 
  | AddAdminAction
  | UpdateAdminPermissionsAction
  | UpdateAdminNameAction
  | RemoveAdminAction;

// Admin asset-scoped action types
interface AdminAssetActionBase extends AdminLifecycleActionBase {
  assetId: string;
}

interface UpdateAdminAnnotationsAction extends AdminAssetActionBase {
  operation: "update_admin_annotations";
  annotations: {
    featured?: boolean;
    malicious?: boolean;
    delisted?: boolean;
    notes?: string;
  };
}

interface DelistAssetAction extends AdminAssetActionBase {
  operation: "delist_asset";
  reason?: string;
}

interface ApproveIconAction extends AdminAssetActionBase {
  operation: "approve_icon";
}

interface RejectIconAction extends AdminAssetActionBase {
  operation: "reject_icon";
  reason?: string;
}

type AdminAssetAction = 
  | UpdateAdminAnnotationsAction
  | DelistAssetAction
  | ApproveIconAction
  | RejectIconAction;

// Submit admin actions
interface SubmitAdminActionOptions {
  action: AdminLifecycleAction;
  adminPubkey?: string;           // Required if not set in client config
  adminPrivateKey?: string | Uint8Array; // Required if not set in client config
}

async function submitAdminAction(
  client: V2Client,
  options: SubmitAdminActionOptions
): Promise<ActionResponse>;

interface SubmitAdminAssetActionOptions {
  assetId: string;
  action: AdminAssetAction;
  adminPubkey?: string;
  adminPrivateKey?: string | Uint8Array;
}

async function submitAdminAssetAction(
  client: V2Client,
  options: SubmitAdminAssetActionOptions
): Promise<ActionResponse>;

// Update admin annotations (non-action endpoint)
interface UpdateAdminAnnotationsOptions {
  assetId: string;
  annotations: {
    featured?: boolean;
    malicious?: boolean;
    delisted?: boolean;
    notes?: string;
  };
}

async function updateAdminAnnotations(
  client: V2Client,
  options: UpdateAdminAnnotationsOptions
): Promise<AdminAnnotationsResponse>;

// Convenience methods
async function addAdmin(
  client: V2Client,
  pubkey: string,
  permissions: string[],
  name?: string
): Promise<ActionResponse>;

async function removeAdmin(
  client: V2Client,
  adminUuid: string
): Promise<ActionResponse>;

async function updateAdminPermissions(
  client: V2Client,
  adminUuid: string,
  permissions: string[]
): Promise<ActionResponse>;

async function delistAsset(
  client: V2Client,
  assetId: string,
  reason?: string
): Promise<ActionResponse>;
```

#### Module 7 - Audit and History

Audit log retrieval:

```typescript
// Asset-specific audit log
interface AssetAuditParams {
  assetId: string;
  page?: number;
  limit?: number;
}

interface AssetAuditResponse {
  auditLog: AuditEntry[];
  pagination: {
    page: number;
    limit: number;
    total: number;
    totalPages: number;
  };
}

async function getAssetAuditLog(
  client: V2Client,
  params: AssetAuditParams
): Promise<AssetAuditResponse>;

// Global audit log
interface GlobalAuditParams {
  page?: number;
  limit?: number;
  assetId?: string;               // Filter by asset
  actionType?: string;            // Filter by action type
}

interface GlobalAuditResponse {
  auditLog: AuditEntry[];
  pagination: {
    page: number;
    limit: number;
    total: number;
    totalPages: number;
  };
}

async function getGlobalAuditLog(
  client: V2Client,
  params?: GlobalAuditParams
): Promise<GlobalAuditResponse>;

// Issuer pubkey history
async function getIssuerPubkeyHistory(
  client: V2Client,
  assetId: string
): Promise<IssuerPubkeyHistoryEntry[]>;
```

#### Module 8 - Signature Utilities

Canonical JSON and cryptographic operations:

```typescript
// Canonical JSON
interface CanonicalJsonOptions {
  indent?: number;
  includeSignatures?: boolean;
}

function canonicalJson(
  obj: unknown,
  options?: CanonicalJsonOptions
): string;

// Signature creation
interface SignOptions {
  privateKey: string | Uint8Array;
  signingContext?: string;        // Default: "liquid-asset-registry-action-v1"
}

async function signData(
  data: string | Uint8Array,
  options: SignOptions
): Promise<string>;              // Return base64-encoded signature

// Signature verification
interface VerifyOptions {
  signature: string;             // Base64-encoded
  pubkey?: string;               // For verify; required for recover
  signingContext?: string;
}

async function verifySignature(
  data: string | Uint8Array,
  options: VerifyOptions
): Promise<boolean>;

// Recover pubkey from signature
async function recoverPubkey(
  data: string | Uint8Array,
  signature: string              // Base64-encoded recoverable signature
): Promise<string>;

// Key generation
async function generateKeyPair(): Promise<{
  privateKey: Uint8Array;
  pubkey: string;                // Hex-encoded
}>;

// Helper for signing actions
interface ActionSignerOptions {
  action: IssuerAction | AdminLifecycleAction | AdminAssetAction;
  privateKey: string | Uint8Array;
  signingContext?: string;
  includeSignature?: boolean;    // Include signature in returned object
}

async function signAction(
  action: ActionSignerOptions
): Promise<{
  canonicalJson: string;
  signature: string;             // Base64-encoded
  signedAction: ActionWithSignature;
}>;
```

#### Module 9 - Error Handling

Consistent error types:

```typescript
// Base error
class RegistryError extends Error {
  constructor(
    message: string,
    options?: {
      code?: string;
      statusCode?: number;
      details?: Record<string, unknown>;
    }
  );
  
  readonly code?: string;
  readonly statusCode?: number;
  readonly details?: Record<string, unknown>;
}

// Specific error types
class SignatureError extends RegistryError {
  constructor(message: string, details?: SignatureErrorDetails);
}

class ValidationError extends RegistryError {
  constructor(message: string, details?: ValidationDetails);
}

class HttpError extends RegistryError {
  constructor(
    message: string,
    statusCode: number,
    body?: unknown
  );
}

class AssetNotFoundError extends RegistryError {
  constructor(assetId: string);
  readonly assetId: string;
}

class DuplicateAssetError extends RegistryError {
  constructor(assetId: string);
  readonly assetId: string;
}

class NonceReplayError extends RegistryError {
  constructor(nonce: string);
  readonly nonce: string;
}

class ActionNoOpError extends RegistryError {
  constructor(operation: string);
  readonly operation: string;
}
```

#### Module 10 - TypeScript Type Definitions

Generate types from OpenAPI:

```bash
# Generate types
npx openapi-typescript openapi.yaml --output src/types/openapi.d.ts
```

Manually add:
- Discriminated union types for actions
- Client-specific types
- Callback types for pagination

#### Module 11 - Documentation and Examples

- README with installation, setup, and usage examples
- JSDoc comments on all public APIs
- Example code in `examples/` directory:
  - `register-asset.ts` - Register a v2 asset
  - `search-assets.ts` - Search and paginate through assets
  - `issuer-action.ts` - Submit a category tag update
  - `admin-action.ts` - Add a new admin
  - `audit-log.ts` - Read audit history
- API reference documentation (Auto-generated from JSDoc)

#### Module 12 - Testing Suite

Unit and integration tests:

```typescript
// Unit tests
- canonicalJson
- signData / verifySignature
- recoverPubkey
- generateKeyPair
- Pagination helpers
- Error parsing

// Integration tests (with real server)
- Asset registration
- Search and lookup
- Issuer actions
- Admin actions
- Audit log retrieval
- Error scenarios
```

## API Surface Summary

### Constructor

```typescript
new AssetRegistryClient(config: ClientConfig)
```

### Client Methods

| Method | Description |
|--------|-------------|
| `client.legacy.register(asset)` | Legacy asset registration |
| `client.legacy.list()` | List all legacy assets |
| `client.legacy.get(assetId)` | Get legacy asset by ID |
| `client.legacy.delete(assetId)` | Deregister legacy asset |
| `client.v2.register(asset)` | v2 asset registration |
| `client.v2.search(params)` | Search v2 assets |
| `client.v2.getAll()` | Get all v2 assets as JSON |
| `client.v2.get(assetId)` | Get v2 asset by ID |
| `client.v2.getAudit(assetId)` | Get asset audit log |
| `client.v2.searchAudit(params)` | Search global audit log |
| `client.v2.submitIssuerAction(assetId, action)` | Submit issuer action |
| `client.v2.migrate(assetId)` | Migrate legacy to v2 |
| `client.v2.submitAdminAction(action)` | Submit admin lifecycle action |
| `client.v2.submitAdminAssetAction(assetId, action)` | Submit admin asset action |
| `client.v2.updateAnnotations(assetId, annotations)` | Update admin annotations |

### Convenience Methods

```typescript
client.v2.replaceCategoryTags(assetId, tags)
client.v2.replaceTradingVenues(assetId, venues)
client.v2.setCustomField(assetId, key, value)
client.v2.deleteCustomField(assetId, key)
client.v2.deregisterAsset(assetId)
client.v2.rotateIssuerKey(assetId, newPubkey)
client.v2.addAdmin(pubkey, permissions, name)
client.v2.updateAdminPermissions(adminUuid, permissions)
client.v2.removeAdmin(adminUuid)
client.v2.delistAsset(assetId, reason)
client.v2.approveIcon(assetId)
client.v2.rejectIcon(assetId, reason)
```

### Static/Utility Functions

```typescript
AssetRegistryClient.canonicalJson(obj)
AssetRegistryClient.sign(data, privateKey)
AssetRegistryClient.verify(data, signature, pubkey)
AssetRegistryClient.recoverPubkey(data, signature)
AssetRegistryClient.generateKeyPair()
AssetRegistryClient.signAction(action, privateKey)
```

## Non-Goals

- **WebSocket support** - Streaming is out of scope for v1
- **Python SDK** - TypeScript only for v1 (separate effort)
- **React hooks** - Separate library that wraps this SDK
- **GraphQL layer** - REST API only

## Acceptance Criteria

- [ ] SDK can be imported in browser and Node.js
- [ ] All API endpoints are accessible through the SDK
- [ ] Signature creation works in browser (Web Crypto API)
- [ ] Signature creation works in Node.js (crypto module)
- [ ] Canonical JSON matches server implementation
- [ ] Pagination helpers work correctly
- [ ] Error handling provides clear messages
- [ ] Full TypeScript coverage with generated types
- [ ] Example code in README works copy-paste
- [ ] Jest tests pass locally
- [ ] Integration tests pass against running server
- [ ] Package publishes to npm with correct entry points

## Open Questions

- Should we support React Query integration out of the box?
- Should we provide a Python SDK in the same repository?
- Should the SDK include a mock server for testing?
- How do we handle secp256k1 in browser vs Node.js?
- Should we bundle dependencies or require users to install them?

