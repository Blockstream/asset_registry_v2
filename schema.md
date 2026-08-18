# Asset Registry V2 Schema Design

This design targets PostgreSQL. It uses surrogate UUID primary keys for registry records and treats the Liquid `asset_id` as blockchain data: indexed and usually unique in practice, but not co-opted as the database identity.

The main shape is action-centered. Accepted issuer, admin, and system actions are stored in one append-only `actions` table. The API's audit log is a projection over that table, ordered by a monotonic `audit_sequence`.

## Design Principles

- Use UUID primary keys for application identity.
- Treat `asset_id` as a semantic blockchain identifier, not as the table primary key.
- Store canonical contract metadata only when needed in append-only action payloads; reconstruct current base contract from projected asset columns for infrequent response needs.
- Normalize issuer mutable metadata that needs search: trading venues, category tags, and custom attributes.
- Store every accepted action in one append-only table so audit history, idempotency, and "last changed by" references all point to the same source.
- Keep controlled string validation in service-layer enums and validators unless there is a later operational need to manage those values in the database.
- Default `domain_verification_method` to `http`.

## UUID Setup

Use one of PostgreSQL's UUID generators:

```sql
create extension if not exists pgcrypto;
```

Most tables use an entity-specific UUID primary key:

```sql
<entity>_uuid uuid primary key default gen_random_uuid()
```

For the `assets` table, use `asset_uuid` as the primary key so `asset_id` can remain reserved for the Liquid blockchain asset ID.

## Core Tables

### `assets`

One registry record for a Liquid asset listing. `asset_id` is indexed but not the primary key.

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| `asset_uuid` | `uuid` | Primary key, default `gen_random_uuid()`. |
| `asset_id` | `char(64)` | Required. Lowercase Liquid asset ID hex. Indexed. |
| `contract_version` | `integer` | Required. Copied from registered or migrated contract version. |
| `domain` | `text` | Required. Copied from `contract.entity.domain`; indexed. |
| `name` | `text` | Required. Copied from `contract.name`; indexed. |
| `ticker` | `text` | Nullable for legacy v0 assets without a ticker; required by the v2 draft; indexed. |
| `precision` | `smallint` | Required. Validate range in application. |
| `contract_extra_fields` | `jsonb` | Required object, default `{}`. Stores legacy contract fields that are contract-hash-relevant but not first-class v2 columns. Native v2 registrations store `{}`. |
| `domain_verification_method` | `text` | Required, default `http`. `dns` or `http`. |
| `initial_issuer_pubkey` | `char(66)` | Required compressed secp256k1 public key. |
| `initial_issuer_pubkey_source` | `text` | Required. `contract`, `registry_registration`, or `migrated_legacy_record`. |
| `current_issuer_pubkey` | `char(66)` | Required. Updated only by accepted key rotation actions. |
| `mutable_schema_version` | `integer` | Required, default `1`. |
| `status` | `text` | Required, default `active`. `active` or `deregistered`. |
| `active_icon_proposal_uuid` | `uuid` | Nullable FK to the one proposal currently published as `icon`. Cleared on deregistration. |
| `created_at` | `timestamptz` | Required, database timestamp. |
| `updated_at` | `timestamptz` | Required, database timestamp. |

Recommended constraints:

```sql
check (asset_id ~ '^[0-9a-f]{64}$')
check (initial_issuer_pubkey ~ '^(02|03)[0-9a-f]{64}$')
check (current_issuer_pubkey ~ '^(02|03)[0-9a-f]{64}$')
check (contract_version >= 0)
check (jsonb_typeof(contract_extra_fields) = 'object')
check (mutable_schema_version >= 1)
```

Legacy contract metadata is also bounded at the application layer before storage: the canonical serialized `contract` object is limited to 4096 bytes by default, configurable with `ASSET_REGISTRY_LEGACY_CONTRACT_MAX_BYTES`. Legacy contracts may contain at most 32 arbitrary extra fields, each serialized extra-field value is limited to 2048 bytes, and the full legacy registration request is limited to 16384 bytes.

`status`, `domain_verification_method`, and `initial_issuer_pubkey_source` are service-level enum values. The database stores them as strings so new values can be introduced without a migration when the service is updated.

Recommended indexes:

```sql
create index assets_asset_id_idx on assets (asset_id);
create index assets_asset_id_prefix_idx on assets (asset_id text_pattern_ops);
create index assets_domain_idx on assets (domain);
create index assets_ticker_idx on assets (ticker text_pattern_ops);
create index assets_name_idx on assets (name text_pattern_ops);
create index assets_ticker_ci_prefix_idx on assets (lower(ticker) text_pattern_ops);
create index assets_name_ci_prefix_idx on assets (lower(name) text_pattern_ops);
create index assets_created_at_idx on assets (created_at desc, asset_uuid);
create index assets_updated_at_idx on assets (updated_at desc, asset_uuid);
```

The current OpenAPI `GET /v2/assets/{asset_id}` response assumes one record per `asset_id`. If the registry wants to allow duplicates later, the API will need a disambiguation rule. A pragmatic interim constraint is one active registry record per `asset_id`, while keeping the UUID primary key:

```sql
create unique index assets_one_active_asset_id_uidx
  on assets (asset_id)
  where status = 'active';
```

If duplicate active `asset_id` rows become a real requirement, remove that index and make lookup return either the newest active row, a conflict, or a list endpoint keyed by `asset_id`.

If ticker uniqueness within an issuer domain should preserve legacy behavior:

```sql
create unique index assets_domain_ticker_active_uidx
  on assets (lower(domain), ticker)
  where status = 'active' and ticker is not null and ticker <> '';
```

### `asset_mutable_metadata`

One lightweight row for mutable metadata versioning. The searchable mutable fields live in child tables.

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| `mutable_metadata_uuid` | `uuid` | Primary key, default `gen_random_uuid()`. |
| `asset_uuid` | `uuid` | Required foreign key to `assets.asset_uuid`; unique. |
| `schema_version` | `integer` | Required, default `1`. |
| `updated_at` | `timestamptz` | Required. |
| `updated_by_action_uuid` | `uuid` | Nullable foreign key to `actions.action_uuid`. |

Recommended constraints and indexes:

```sql
unique (asset_uuid)
check (schema_version >= 1)
create index asset_mutable_metadata_action_idx on asset_mutable_metadata (updated_by_action_uuid);
```

### `asset_trading_venues`

Current `mutable.trading_venues`, stored without redundant JSON in `asset_mutable_metadata`.

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| `trading_venue_uuid` | `uuid` | Primary key, default `gen_random_uuid()`. |
| `asset_uuid` | `uuid` | Required foreign key to `assets.asset_uuid`. |
| `name` | `text` | Required venue identifier, e.g. `sideswap`, `bitfinex`. |
| `url` | `text` | Required normalized URI. |
| `position` | `integer` | Required, preserves response array order. |
| `created_at` | `timestamptz` | Required. |
| `updated_at` | `timestamptz` | Required. |
| `updated_by_action_uuid` | `uuid` | Nullable foreign key to `actions.action_uuid`. |

Recommended constraints and indexes:

```sql
unique (asset_uuid, url)
unique (asset_uuid, name, url)
create index asset_trading_venues_name_idx on asset_trading_venues (name, asset_uuid);
create index asset_trading_venues_asset_position_idx on asset_trading_venues (asset_uuid, position);
```

The API field is still returned as:

```json
{"venue":"sideswap","url":"https://api.sideswap.io/assets/ABT"}
```

The table column is named `name` because that is the requested database shape; application serialization maps `name` to API `venue`.

### `asset_category_tags`

Current `mutable.category_tags`, normalized for direct indexing and search.

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| `category_tag_uuid` | `uuid` | Primary key, default `gen_random_uuid()`. |
| `asset_uuid` | `uuid` | Required foreign key to `assets.asset_uuid`. |
| `tag` | `text` | Required category tag. |
| `position` | `integer` | Required, preserves response array order. |
| `created_at` | `timestamptz` | Required. |
| `updated_by_action_uuid` | `uuid` | Nullable foreign key to `actions.action_uuid`. |

Recommended constraints and indexes:

```sql
unique (asset_uuid, tag)
create index asset_category_tags_tag_idx on asset_category_tags (tag, asset_uuid);
create index asset_category_tags_asset_position_idx on asset_category_tags (asset_uuid, position);
```

### `asset_custom_attributes`

Current `mutable.custom`, normalized by key. Values remain JSONB because the draft permits arbitrary JSON values.

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| `custom_attribute_uuid` | `uuid` | Primary key, default `gen_random_uuid()`. |
| `asset_uuid` | `uuid` | Required foreign key to `assets.asset_uuid`. |
| `name` | `text` | Required custom property name, e.g. `isin`. |
| `value` | `jsonb` | Required custom property value. |
| `created_at` | `timestamptz` | Required. |
| `updated_at` | `timestamptz` | Required. |
| `updated_by_action_uuid` | `uuid` | Nullable foreign key to `actions.action_uuid`. |

Recommended constraints and indexes:

```sql
unique (asset_uuid, name)
create index asset_custom_attributes_name_idx on asset_custom_attributes (name, asset_uuid);
create index asset_custom_attributes_value_gin_idx
  on asset_custom_attributes using gin (value jsonb_path_ops);
```

If custom values should remain unsearchable, skip the `value` GIN index and only index `name`.

### `asset_admin_annotations`

Registry-operated annotations and moderation state. These fields are not issuer signed.

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| `admin_annotation_uuid` | `uuid` | Primary key, default `gen_random_uuid()`. |
| `asset_uuid` | `uuid` | Required foreign key to `assets.asset_uuid`; unique. |
| `asset_type` | `text` | Nullable service-validated asset type, e.g. `AMP_asset`, `stablecoin`, `security_token`, `other`. |
| `featured` | `boolean` | Required, default `false`. |
| `malicious` | `boolean` | Required, default `false`. |
| `delisted` | `boolean` | Required, default `false`. |
| `admin_notes` | `text` | Nullable, max length enforced by application. |
| `last_admin_action_uuid` | `uuid` | Nullable foreign key to `actions.action_uuid`. |
| `updated_at` | `timestamptz` | Required. |
| `updated_by_admin_id` | `text` | Nullable admin identifier. |

Recommended indexes:

```sql
unique (asset_uuid)
create index asset_admin_asset_type_idx on asset_admin_annotations (asset_type);
create index asset_admin_featured_idx on asset_admin_annotations (featured) where featured = true;
create index asset_admin_malicious_idx on asset_admin_annotations (malicious) where malicious = true;
create index asset_admin_delisted_idx on asset_admin_annotations (delisted) where delisted = true;
create index asset_admin_last_action_idx on asset_admin_annotations (last_admin_action_uuid);
```

`AdminActionSummary` can be assembled by joining `last_admin_action_uuid` to `actions.action_uuid`.

## Asset Icon Proposals

### `asset_icon_proposals`

Stores issuer proposals, admin decisions, imported legacy approvals, and historical PNG bytes.

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| `icon_proposal_uuid` | `uuid` | Primary key. |
| `asset_uuid` | `uuid` | Required FK to `assets`; cascades on asset deletion. |
| `icon_hash` | `char(64)` | SHA-256 of decoded PNG bytes. |
| `image_data` | `bytea` | Present while pending and retained for approved icons so they can be reused later. |
| `status` | `text` | `pending`, `rejected`, or `approved`. |
| `submission_method` | `text` | `v2_issuer_signature`, `admin_upload`, or `legacy_import`. |
| `proposed_by_action_uuid` | `uuid` | Required FK to the issuer/system action. |
| `decided_by_action_uuid` | `uuid` | Nullable FK to the admin/system decision action. |
| `proposed_at`, `decided_at` | `timestamptz` | Registry-observed lifecycle timestamps. |
| `obsoleted_at` | `timestamptz` | Nullable timestamp set when the associated asset registration is deregistered. Orthogonal to review status. |
| `obsoleted_by_action_uuid` | `uuid` | Nullable FK to the deregistration action that made the proposal obsolete. |

A partial unique index allows one non-obsolete pending proposal per asset registration. The active icon is selected only by `assets.active_icon_proposal_uuid`; approved image bytes and review statuses remain intact when another icon becomes active.

## Issuer Key History

### `issuer_pubkey_history`

Represents key validity intervals returned by `issuer_pubkey_history`.

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| `issuer_pubkey_history_uuid` | `uuid` | Primary key, default `gen_random_uuid()`. |
| `asset_uuid` | `uuid` | Required foreign key to `assets.asset_uuid`. |
| `pubkey` | `char(66)` | Required compressed secp256k1 public key. |
| `valid_from_action_uuid` | `uuid` | Required foreign key to `actions.action_uuid`. |
| `valid_until_action_uuid` | `uuid` | Nullable foreign key to `actions.action_uuid`. Null means current. |
| `created_at` | `timestamptz` | Required. |

Recommended constraints and indexes:

```sql
create unique index issuer_pubkey_history_one_current_uidx
  on issuer_pubkey_history (asset_uuid)
  where valid_until_action_uuid is null;
create index issuer_pubkey_history_asset_idx on issuer_pubkey_history (asset_uuid, created_at);
```

The API wants `valid_from_audit_id` and `valid_until_audit_id`. Those can be derived by joining each action UUID to `actions.audit_sequence`.

## Actions and Audit Projection

### `actions`

Append-only table for issuer, admin, and system actions. This replaces separate `issuer_action_nonces` and `audit_log` tables.

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| `action_uuid` | `uuid` | Primary key, default `gen_random_uuid()`. |
| `audit_sequence` | `bigserial` | Unique monotonic sequence used as API `audit_id`. |
| `asset_uuid` | `uuid` | Required foreign key to `assets.asset_uuid`. |
| `asset_chain_id` | `char(64)` | Denormalized blockchain asset ID at action time for easier audit search. |
| `actor` | `text` | Required. `issuer`, `admin`, or `system`. |
| `operation` | `text` | Required. Issuer operation or admin/system operation name. |
| `action` | `jsonb` | Required. Canonical issuer action object, or server-defined admin/system action object. |
| `signature` | `text` | Nullable for admin/system; required for issuer actions. |
| `nonce` | `text` | Nullable. Required for issuer actions. |
| `issuer_timestamp` | `timestamptz` | Nullable. Copied from issuer action timestamp. |
| `verified_pubkey` | `char(66)` | Nullable. Public key that verified issuer signature. |
| `admin_id` | `text` | Nullable. Admin identifier for admin actions. |
| `server_received_at` | `timestamptz` | Required database timestamp. |
| `created_at` | `timestamptz` | Required. |

Recommended constraints and indexes:

```sql
unique (audit_sequence)
create unique index actions_issuer_nonce_uidx
  on actions (asset_uuid, nonce)
  where actor = 'issuer';
create index actions_asset_sequence_idx on actions (asset_uuid, audit_sequence);
create index actions_chain_asset_sequence_idx on actions (asset_chain_id, audit_sequence);
create index actions_operation_sequence_idx on actions (operation, audit_sequence);
create index actions_actor_sequence_idx on actions (actor, audit_sequence);
create index actions_received_at_idx on actions (server_received_at, audit_sequence);
```

`actor` and `operation` are service-level enum values. The database stores them as strings so operation additions do not require schema migrations.

Nonce idempotency:

- If an issuer action arrives with a new `(asset_uuid, nonce)`, insert it.
- If `(asset_uuid, nonce)` already exists, reconstruct the canonical JSON for the existing `action` and compare it with the canonical JSON for the incoming body.
- If the canonical payloads match and the signature matches, return the existing action as `idempotent_retry`.
- If they differ, reject with `nonce_conflict`.

I do not recommend storing both a parsed action and raw canonical action bytes if the application has one canonicalization implementation and always reconstructs signed payloads from the stored `action` JSONB. The only reason to keep bytes or a SHA-256 digest would be operational defensiveness: it gives a cheap exact comparison and protects against future canonicalization bugs or JSON number-format edge cases. The draft's signed actions mostly use strings, integers, arrays, and objects, so JSONB plus deterministic canonical serialization is reasonable.

The API's `AuditEntry` is assembled directly from `actions`:

- `audit_id` = `actions.audit_sequence`
- `server_received_at` = `actions.server_received_at`
- `actor`, `verified_pubkey`, `admin_id`, `action`, and `signature` come from the same row

This keeps audit as a read model, not a separate first-class entity. The remaining reason to create a separate audit table later would be if you need to audit low-level state changes that are not actions, such as automated repair jobs, failed attempts, or per-field before/after diffs. For this API draft, `actions` is enough.

## Registration Flow

In one transaction:

1. Verify chain commitment, contract hash, and domain proof.
2. Insert into `assets`.
3. Insert default row into `asset_mutable_metadata`.
4. Insert provided mutable metadata into `asset_trading_venues`, `asset_category_tags`, and `asset_custom_attributes`.
5. Insert default row into `asset_admin_annotations`.
6. Insert a registration row into `actions` with actor `system` or `issuer`, depending on how registration is authenticated.
7. Insert initial row into `issuer_pubkey_history`, referencing the registration action.
8. Commit the transaction; the latest action is derived from `actions.audit_sequence`.

For v2 contracts, `initial_issuer_pubkey_source = 'contract'`. For legacy registrations where the key is outside the contract, use `registry_registration` or `migrated_legacy_record`.

## Issuer Action Flow

In one transaction:

1. Parse the submitted JSON body and reject it if it is not canonical.
2. Resolve the URL `asset_id` to the target `assets.asset_uuid`.
3. Verify URL `asset_id` equals action `asset_id`.
4. Load `assets.current_issuer_pubkey` and verify the signature over canonical action JSON.
5. Check timestamp freshness against the latest accepted issuer action projection.
6. Insert into `actions`; on `(asset_uuid, nonce)` conflict, apply idempotency logic.
7. Apply operation-specific state changes:
   - `replace /mutable/category_tags`: replace rows in `asset_category_tags`.
   - `replace /mutable/trading_venues`: replace rows in `asset_trading_venues`.
   - `replace /mutable/custom/{key}`: upsert one row in `asset_custom_attributes`.
   - `delete /mutable/custom/{key}`: delete one row from `asset_custom_attributes`.
   - `deregister`: set `assets.status = 'deregistered'`.
   - `rotate_issuer_pubkey`: update `assets.current_issuer_pubkey` and close/open `issuer_pubkey_history` rows.
   - `migrate_contract_metadata`: update `assets.contract_version` and projected contract columns only after validating migration rules.
   - `migrate_mutable_schema`: update `assets.mutable_schema_version` and transformed mutable rows.
8. Set affected `updated_by_action_uuid` fields to the inserted `actions.action_uuid`.
9. Update `assets.updated_at`.

## Response Assembly

`AssetResponse` is assembled from:

- `assets` for identity, reconstructable contract fields, issuer keys, status, and timestamps.
- `asset_trading_venues`, `asset_category_tags`, and `asset_custom_attributes` for `mutable`.
- `asset_admin_annotations` plus `actions` for `admin.last_admin_action`.
- `issuer_pubkey_history` joined to `actions` for audit sequence values.

`AuditLogResponse` is assembled from `actions`, ordered by `audit_sequence`.

## Open Questions

- Whether initial registration should be issuer signed. The draft requires signed issuer mutations but does not require registration itself to be signed.
- Whether migrated historical contract metadata should be versioned in a separate `asset_contract_versions` table. A separate table is safer if historical contract responses or forensic migration review are required.
- Legacy v0 assets may omit `ticker`; v2 contracts still require it.
- How the API should behave if duplicate active rows with the same blockchain `asset_id` are allowed later. `GET /v2/assets/{asset_id}` currently implies a single result.
