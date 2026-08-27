# AGENTS.md

This file provides guidance to coding agents when working with this repository.

## cSpell Ignore List

Word list for false positives in cSpell: Pydantic, Esplora, psycopg, pytest, mypy, uvicorn, keypair, keypairs, Blockstream, psql, contextlib, contextmanager

## Project Overview

Liquid Asset Registry v2 - A FastAPI + PostgreSQL service for managing Liquid network assets with v2-style registration and admin governance. The service maintains compatibility with the legacy v1 API while implementing the new v2 registry model.

## Architecture

### Project Structure

```
registry_api/
├── main.py              # FastAPI app, middleware, exception handlers, router registration
├── db.py                # SQLAlchemy engine and session factory
├── settings.py          # Pydantic settings with env vars
├── models.py            # SQLAlchemy ORM models for all database tables
├── schemas.py           # Pydantic models for API request/response validation
├── v2_assets.py         # v2 API handlers (register, search, lookup, audit, actions)
├── legacy.py            # v1 API handlers (register, list, lookup, deregister)
├── signatures.py        # Canonical JSON and signature verification
├── admin_actions.py     # Signed admin governance bootstrap, verification, permissions, lifecycle actions
├── chain.py             # Chain verification (Esplora API backend)
├── domain_verification.py # Domain proof verification (HTTP/DNS)
├── shadow.py            # Legacy shadow forwarding for compatibility
├── admin.py             # Signed admin annotation updates
├── audit.py             # Audit log projection from asset-scoped actions table
├── migration.py         # v1 to v2 migration endpoint
├── legacy_assets.py     # Legacy asset retrieval/deregistration
├── canonical_json.py    # Canonical JSON for signed actions
├── errors.py            # RegistryError and ErrorResponse
├── observability.py     # Logging middleware and request IDs
├── validation.py        # Asset ID, pubkey, domain, custom attribute validators
├── http_clients.py      # HTTP client wrapper for external API calls
└── api/
    ├── __init__.py      # Router registrations
    ├── health.py        # Health check endpoint
    ├── legacy.py        # Legacy API routes
    └── v2.py            # v2 API routes
```

### Database Schema (PostgreSQL)

See `schema.md` for full design. Key tables:

| Table | Purpose |
|-------|---------|
| `assets` | Registry records for Liquid assets; UUID primary key (`asset_uuid`), blockchain ID indexed (`asset_id`) |
| `asset_mutable_metadata` | Versioned metadata root for each asset |
| `asset_trading_venues` | Trading venue data (normalized rows) |
| `asset_category_tags` | Category tag data (normalized rows) |
| `asset_custom_attributes` | Custom attributes (key-value rows) |
| `asset_admin_annotations` | Admin moderation state (featured, malicious, delisted) |
| `issuer_pubkey_history` | Issuer key rotation history with validity intervals |
| `actions` | Append-only audit log; one row per accepted action; `audit_sequence` is primary ordering key |
| `admin_keys` | Admin identity (secp256k1 pubkey); `status: active|removed` |
| `admin_permissions` | Admin permission assignments |
| `admin_actions` | Admin management actions (lifecycle only) |
| `audit_sequence_global` | PostgreSQL sequence for global audit ordering |

Legacy contract fields that are not first-class v2 columns are stored in `assets.contract_extra_fields` so migrated legacy contracts can be reconstructed without making native v2 registration contracts accept arbitrary fields.

### API Structure

**Legacy (v1)** - `GET /docs#/Legacy`
- `POST /` - Register asset (legacy format)
- `GET /` - List all assets
- `GET /{asset_id}` - Get asset
- `DELETE /{asset_id}` - Deregister asset

**v2** - `GET /docs#/v2`
- `POST /assets` - Register asset (v2 format with contract JSON)
- `GET /assets` - Search assets (pagination, filters)
- `GET /assets/{asset_id}` - Get asset
- `GET /assets/all.json` - All assets as flat JSON
- `GET /assets/{asset_id}/audit` - Asset audit log
- `GET /audit` - Global audit log
- `POST /assets/{asset_id}/actions` - Submit issuer action
- `POST /assets/{asset_id}/migrate` - Migrate legacy to v2
- `POST /admin/actions` - Submit signed admin lifecycle action
- `POST /admin/assets/{asset_id}/actions` - Submit signed asset-scoped admin action such as forced delist/relist
- `PUT /admin/assets/{asset_id}/annotations` - Update admin annotations

### Key Design Patterns

**Action-Centric Model**: The API is modeled around actions - every state change is an action in the `actions` table. The audit log is a projection from `actions`, ordered by `audit_sequence`.

**UUID Primary Keys**: Database tables use UUID primary keys for application identity. Blockchain IDs (`asset_id`, `pubkey`, `nonce`) are indexed but not primary keys.

**Dual-Mode Authorization**:
- Issuer actions: Signed with asset's issuer pubkey (`Asset-Registry-Signature` header)
- Admin actions: Bitcoin Signed Message signature in `Asset-Registry-Admin-Signature`; signed canonical JSON includes `actor_pubkey`, which the server verifies and authorizes
- System actions: No signature required

**Shadow Forwarding**: When `ASSET_REGISTRY_LEGACY_SHADOW_WRITE=true`, supported legacy writes are forwarded to the original registry (`ASSET_REGISTRY_LEGACY_BASE_URL`) after local persistence. The legacy registry status code is returned when available.

**Validation & Hooks**:
- Contract hash verification against v2 contract metadata
- Chain verification (issuance commitment via Esplora API)
- Domain verification (DNS TXT or HTTP proof)

**Canonical JSON**: All v2 issuer and admin actions use canonical JSON (deterministic serialization). Signed payloads are verified against canonical form.

**Wallycore Required for Liquid Primitives**: Any code involving Liquid transaction parsing or serialization, Liquid data structures, hashing algorithms, signing algorithms, signature verification, or related protocol primitives MUST use `wallycore` whenever `wallycore` can perform the operation. Do not implement or retain custom parsing, serialization, hashing, or signing logic for functionality provided by `wallycore`.

### SQLAlchemy Models (models.py)

Each model has:
- Primary key (UUID)
- `TimestampMixin` (created_at, updated_at)
- Relationships to parent/child tables
- Check constraints for controlled strings
- Indexes for common queries

### Database Layer (db.py)

- `engine`: SQLAlchemy engine with `pool_pre_ping=True`
- `SessionLocal`: Session factory
- `get_db()`: Dependency for FastAPI

## API Routes by Module

| Route | Method | Handler | Module |
|-------|--------|---------|--------|
| `/health` | GET | `get_health()` | api/health.py |
| `/` | POST | `register_asset_legacy_root()` | api/legacy.py |
| `/` | GET | `list_assets_legacy_root()` | api/legacy.py |
| `/{asset_id}` | GET | `get_asset_legacy_root()` | api/legacy.py |
| `/{asset_id}` | DELETE | `delete_asset_legacy_root()` | api/legacy.py |
| `/v2/assets` | POST | `register_asset_v2()` | api/v2.py |
| `/v2/assets` | GET | `search_assets_v2()` | api/v2.py |
| `/v2/assets/all.json` | GET | `all_assets_v2_json()` | api/v2.py |
| `/v2/assets/{asset_id}` | GET | `get_asset_v2()` | api/v2.py |
| `/v2/assets/{asset_id}/audit` | GET | `get_asset_audit_v2()` | api/v2.py |
| `/v2/audit` | GET | `search_audit_v2()` | api/v2.py |
| `/v2/assets/{asset_id}/actions` | POST | `submit_asset_issuer_action_v2()` | api/v2.py |
| `/v2/assets/{asset_id}/migrate` | POST | `migrate_legacy_asset()` | api/v2.py |
| `/v2/admin/actions` | POST | `submit_admin_action_v2()` | api/v2.py |
| `/v2/admin/assets/{asset_id}/actions` | POST | `submit_admin_asset_action_v2()` | api/v2.py |
| `/v2/admin/assets/{asset_id}/annotations` | PUT | `update_admin_annotations_v2()` | api/v2.py |

## Testing

### Running Tests

```bash
# All tests
pytest

# Specific test file
pytest tests/test_xxx.py

# Specific test function
pytest tests/test_xxx.py::test_function_name

# With docker-compose
docker-compose up -d
pytest

# With test fixtures
pytest tests/test_xxx.py --features
```

### Test Fixtures

- `db_seed()` - Seed database with test assets
- `db_seed_legacy() - Seed with legacy assets
- `generate_admin_pair()` - Generate test admin keypair

## Configuration

### Environment Variables (ASSET_REGISTRY_ prefix)

| Variable | Description | Default |
|----------|-------------|---------|
| `ASSET_REGISTRY_ENVIRONMENT` | `development` or `production` | development |
| `ASSET_REGISTRY_DEBUG` | Debug mode | False |
| `ASSET_REGISTRY_LOG_LEVEL` | Logging level | INFO |
| `ASSET_REGISTRY_DATABASE_URL` | PostgreSQL connection URL | postgresql+psycopg://... |
| `ASSET_REGISTRY_NETWORK` | Liquid network | liquid |
| `ASSET_REGISTRY_ESPLORA_URL` | Blockstream API URL | https://blockstream.info/liquid/api |
| `ASSET_REGISTRY_MAX_REQUEST_BODY_BYTES` | Max HTTP request body size; 0 allowed only when JSON depth guard is disabled | 1048576 |
| `ASSET_REGISTRY_MAX_JSON_DEPTH` | Max JSON request nesting depth; 0 disables the guard | 100 |
| `ASSET_REGISTRY_DNS_OVER_HTTPS_URL` | DNS resolver | https://dns.google/resolve |
| `ASSET_REGISTRY_ENFORCE_CHAIN_VERIFICATION` | Enforce chain proofs | True |
| `ASSET_REGISTRY_ENFORCE_DOMAIN_VERIFICATION` | Enforce domain proofs | True |
| `ASSET_REGISTRY_LEGACY_BASE_URL` | Legacy registry URL | https://assets.blockstream.info |
| `ASSET_REGISTRY_LEGACY_SHADOW_WRITE` | Forward supported legacy writes | False |
| `ASSET_REGISTRY_LEGACY_TIMEOUT_SECONDS` | Shadow timeout | 10 |
| `ASSET_REGISTRY_LEGACY_FAILURE_SANITY_DELAY_SECONDS` | Delay after v1 failure | 5 |
| `ASSET_REGISTRY_REGISTRATION_RATE_LIMIT` | Max registration/migration requests per client IP per window; 0 disables | 30 |
| `ASSET_REGISTRY_REGISTRATION_RATE_LIMIT_WINDOW_SECONDS` | Rate-limit window | 60 |
| `ASSET_REGISTRY_DOMAIN_FETCH_FAILURE_COOLDOWN_SECONDS` | Cooldown before re-fetching a domain proof that recently failed; 0 disables | 30 |
| `ASSET_REGISTRY_DOMAIN_FETCH_QUOTA` | Max proof fetches per domain per window; 0 disables | 20 |
| `ASSET_REGISTRY_DOMAIN_FETCH_QUOTA_WINDOW_SECONDS` | Per-domain quota window | 60 |
| `ASSET_REGISTRY_MAX_CONCURRENT_PROOF_FETCHES` | Max concurrent outbound domain proof fetches; 0 disables | 16 |
| `ASSET_REGISTRY_GENESIS_ADMIN_PUBKEY` | Bootstrap root admin pubkey when no admins exist | None |

Uvicorn is the single source of truth for proxy-aware client addresses. The container enables proxy-header handling, and Uvicorn trusts forwarded headers only from peers listed in its unprefixed `FORWARDED_ALLOW_IPS` environment variable (`127.0.0.1` by default). Registration rate limiting and request logs both use the resulting `request.client.host` value.

### Settings Access

```python
from registry_api.settings import get_settings
settings = get_settings()
```

## Database Migrations (Alembic)

### Running Migrations

```bash
# Apply all migrations
alembic upgrade head

# Apply to specific revision
alembic upgrade +ids.99999

# Rollback one revision
alembic downgrade -1

# Check migration files
alembic history

# Generate migration
alembic revision --autogenerate -m "description"
```

### Migration Strategies

- **Create**: Add new tables, columns, indexes
- **Alter column**: Change type, length, nullable status, server_default
- **Drop**: Remove tables

## Code Quality

### Linting

```bash
ruff check registry_api tests
ruff format registry_api tests
```

### Type Checking

```bash
mypy registry_api
```

## Development Commands

```bash
# Start dev server
uvicorn registry_api.main:app --reload --port 8000

# Start with docker-compose
docker-compose up

# Run database migrations
alembic upgrade head

# Run tests
pytest

# Watch mode for dev
uvicorn registry_api.main:app --reload --port 8000 --watch

# Shell to database
docker-compose exec postgres psql -U asset_registry -d asset_registry
```

## Implementation Status (as of 2026-05-05)

Completed:
- `[x]` Module 1 - Service Scaffold
- `[x]` Module 2 - Database Schema and Migrations
- `[x]` Module 3 - Shared Domain and Validation Layer
- `[x]` Module 4 - Legacy v1 Registration
- `[x]` Module 5 - Legacy Compatibility Write Gate
- `[x]` Module 6 - Legacy v1 Lookup and Listing
- `[x]` Module 7 - Legacy v1 Deregistration
- `[x]` Module 8 - v1-to-v2 Registry Migration
- `[x]` Module 9 - v2 Registration
- `[x]` Module 10 - v2 Search, Listing, and Lookup
- `[x]` Module 11 - v2 Issuer Actions
- `[x]` Module 12 - Issuer Key Rotation
- `[x]` Module 13 - Admin Annotations
- `[x]` Module 14 - Audit Projection
- `[x]` Module 15 - OpenAPI Spec Alignment
- `[~]` Module 16 - Operational Readiness (partial)

## Admin Governance (Module 2+)

Admin operations now use signed actions instead of shared bearer tokens:

| Operation | Permission |
|-----------|------------|
| `add_admin` | `manage_admins`, or `root` when granting `root`; creates a new admin or reactivates a removed admin, and conflicts with an active pubkey |
| `update_admin_permissions` | `manage_admins`, or `root` when granting/revoking `root` |
| `update_admin_name` | `manage_admins` |
| `remove_admin` | `manage_admins` (non-root) or `root` (any) |
| `update_admin_annotations` | `annotate_assets` or `root` |
| `delist_assets` | `delist_assets` or `root` |
| `approve_icon` | `review_icons` |
| `reject_icon` | `review_icons` |
| `set_icon` | `manage_icons` |

Admin lifecycle actions use `admin_actions` table with:
- `actor_admin_uuid`: Admin identity (FK to admin_keys)
- `actor_pubkey`: Signing pubkey declared in the signed action payload and verified against the signature
- `operation`: Action type
- `signature`: Base64 compact Bitcoin Signed Message signature over canonical JSON
- `nonce`: Request-scoped nonce
- `admin_timestamp`: Signer timestamp (freshness check)
- `server_received_at`: Observed timestamp

Admin keys are managed through lifecycle actions (`add_admin`, `update_admin_permissions`, `update_admin_name`, `remove_admin`). `add_admin` rejects an active admin with the same pubkey using `admin_conflict`; a removed admin is reactivated with the submitted friendly name and permissions. The genesis admin key is bootstrapped on admin endpoint access if no admins exist and `ASSET_REGISTRY_GENESIS_ADMIN_PUBKEY` is configured.

## Secrets to Avoid

- Database passwords in code (use environment variables)
- Private keys

## Common Patterns

### Database Transactions

Service-layer functions wrap database writes in transactions. Failed writes roll back before errors are returned to the caller.

```python
from contextlib import contextmanager
from sqlalchemy import event

@contextmanager
def transaction_scope(db: Session):
    try:
        db.begin()
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
```

### Validation Errors

Use specific error codes from `errors.py`:

```python
from registry_api.errors import RegistryError

def validate(asset_id: str):
    if len(asset_id) != 64:
        raise RegistryError("validation_error", "asset_id must be 64 characters")
```

Error codes include: `asset_not_found`, `asset_conflict`, `invalid_signature`, `nonce_replay`, `invalid_pubkey`, etc.

### Response Logging

Request logging is middleware-level. Each request logs: method, path, status code, request_id, duration.

## Migration from v1 to v2

1. Call `POST /v2/assets/{asset_id}/migrate`
2. Sets `initial_issuer_pubkey_source = "migrated_legacy_record"`
3. Inserts `migrate_contract_metadata` action
4. Preserves blockchain identity (asset_id, contract hash, issuance commitment)
5. Idempotent (same asset_id can be migrated multiple times)

## OpenAPI Alignment

FastAPI routes and Pydantic models are the source of truth for OpenAPI. `openapi.yaml` is a generated snapshot for SDKs, reviews, and releases; do not edit it manually.

After changing routes, request/response models, examples, or OpenAPI metadata, run:

```bash
.venv/bin/python scripts/generate_openapi.py
(cd sdk && npm run generate:types)
.venv/bin/python scripts/generate_openapi.py --check
(cd sdk && npm run check:types)
```

Always regenerate and commit both `openapi.yaml` and `sdk/src/generated/openapi.d.ts` when the API specification changes.

`tests/test_openapi_alignment.py` compares the tracked snapshot with `create_app().openapi()` and verifies stable operation IDs and examples. Service design notes live in `ARCHITECTURE.md`.

## Alembic Notes

Alembic revision IDs must stay within the default `alembic_version.version_num` length of 32 characters unless the version table column is explicitly widened.

## Planning Documents

Implementation plans should live in `plans/` and use the existing module-based format unless explicitly requested otherwise.

Plan files should be named with a date prefix and short topic slug, such as `YYYY-MM-DD-short-topic.md`. Plans should include:
- title and `Date: YYYY-MM-DD`
- `## Implementation Status` with the standard `[x]`, `[~]`, `[ ]` legend
- a `Current progress` checklist of digestible modules
- `## Purpose` and any relevant design decision sections
- `## Implementation Modules` with each module broken into concrete checklist items

## Next Steps

See `plans/` directory for detailed implementation plans.
