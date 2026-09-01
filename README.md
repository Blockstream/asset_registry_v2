# Liquid Asset Registry v2

The Liquid Asset Registry is Blockstream's official registry for assets issued
on the Liquid sidechain. Built with FastAPI and PostgreSQL, it supports asset
registration and discovery, signed issuer updates, admin governance, and
append-only audit history while retaining compatibility with the legacy v1
API.

The Python API implementation lives under `registry_api/`, database migrations
under `migrations/`, and the generated API specification in `openapi.yaml`.

## TypeScript SDK

The repository also includes a TypeScript SDK for Node.js and modern browsers.
It provides typed clients for the v2 and legacy-compatible APIs, along with
helpers for signing registrations and issuer actions. See the
[SDK README](sdk/README.md) for installation and usage.

## Domain Verification Trust Model

Domain verification is a registry acceptance check performed at registration time.

Audit entries record the registry's decision and accepted action payloads. They do not, by themselves, provide independently verifiable proof that the domain served the challenge at that time. Consumers who need that stronger guarantee would need an additional witness, transparency, DNSSEC, zkTLS, or notary-style proof system outside the current baseline API.

## Local Development

Start PostgreSQL and the API container:

```bash
docker compose up -d
```

Apply migrations:

```bash
ASSET_REGISTRY_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry \
  .venv/bin/alembic upgrade head
```

Run the test suite without live PostgreSQL-backed tests:

```bash
.venv/bin/python -m pytest
```

Run the full live PostgreSQL-backed test suite:

```bash
ASSET_REGISTRY_TEST_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry \
  .venv/bin/pytest
```

Regenerate the OpenAPI snapshot after changing routes or schemas:

```bash
.venv/bin/python scripts/generate_openapi.py
.venv/bin/python scripts/generate_openapi.py --check
```

FastAPI and Pydantic are the source of truth. Do not edit `openapi.yaml` manually.

## Genesis Admin

The first deployment can bootstrap a root admin from the
`ASSET_REGISTRY_GENESIS_ADMIN_PUBKEY` setting. Generate a new admin keypair
from the repository root:

```bash
.venv/bin/python scripts/generate_admin_keypair.py
```

The command outputs `private_key` and `public_key` as lowercase hex. Keep the
private key secret; it is required to sign admin actions. Configure the public
key as the genesis admin:

```bash
ASSET_REGISTRY_GENESIS_ADMIN_PUBKEY=<public_key>
```

When an admin-protected endpoint is first accessed, the configured key becomes
the root admin if the registry does not contain any admins. The root admin can
then register additional admins using the public keys they generate with the
same script. Each admin must retain their own private key and share only their
public key.

See [configuration](docs/configuration.md) and
[deployment notes](docs/deployment.md) for related operational settings.

## Asset Icons

V2 asset responses expose an approved icon as a registry-relative,
content-addressed URL under `icon.href`; assets without an approved icon return
`icon: null`. Resolve the URL against the registry base URL and cache the linked
PNG freely: hashed icon responses use immutable caching, and publishing a
replacement produces a new URL.

`GET /v2/assets/{asset_id}/icon` provides a stable per-asset URL that redirects
to the current content-addressed icon. The legacy `/icons.json` endpoint remains
available for clients that need a bulk Base64 map.

## Legacy Compatibility Limits

Legacy contracts may retain arbitrary extra fields for v1 compatibility;
native v2 contracts remain strict. Validation bounds legacy input as follows:

- The canonical serialized `contract` object is limited to 4096 bytes by
  default. `ASSET_REGISTRY_LEGACY_CONTRACT_MAX_BYTES` can temporarily raise this
  limit during a migration or shadow-write rollout.
- A legacy contract may contain at most 32 extra fields, with each serialized
  extra-field value limited to 2048 bytes.
- The complete legacy registration request is limited to 16384 bytes.

See [configuration](docs/configuration.md) for the related deployment settings.

## Key Files

- `AGENTS.md` - repo guidance for coding agents.
- `ARCHITECTURE.md` - service design, trust boundaries, and compatibility model.
- `CONTRIBUTING.md` - contribution workflow and development checks.
- `SECURITY.md` - private vulnerability-reporting instructions.
- `schema.md` - database schema notes.
- `docs/configuration.md` - environment variables.
- `docs/deployment.md` - deployment notes.
- `plans/` - implementation plans and sequencing notes.
- `openapi.yaml` - generated OpenAPI snapshot for SDKs, reviews, and releases.

## License

Licensed under the [MIT License](LICENSE).
