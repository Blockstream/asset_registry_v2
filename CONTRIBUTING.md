# Contributing

Thank you for contributing to the Liquid Asset Registry.

## Before You Start

- Search existing issues and pull requests before opening a duplicate.
- Use an issue to discuss substantial behavior or API changes before investing
  in an implementation.
- Report vulnerabilities privately according to [SECURITY.md](SECURITY.md).

## Development Setup

The service requires Python 3.11 or newer and PostgreSQL 16. The SDK requires
Node.js 18.18 or newer.

Install the Python development dependencies:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

`requirements.txt` is the fully pinned, hash-verified production dependency
lock used by the container build. After changing runtime dependencies in
`pyproject.toml`, regenerate it with:

```bash
uv pip compile pyproject.toml --python-platform x86_64-manylinux_2_28 \
  --generate-hashes --python-version 3.12 --output-file requirements.txt
```

Start PostgreSQL and apply migrations:

```bash
docker compose up -d postgres
ASSET_REGISTRY_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry \
  .venv/bin/alembic upgrade head
```

Install SDK dependencies when working under `sdk/`:

```bash
cd sdk
npm ci
```

## Making Changes

- Keep changes focused and include tests for changed behavior.
- Use `wallycore` for supported Liquid transaction, serialization, hashing,
  signing, and signature-verification primitives.
- Treat FastAPI and Pydantic as the OpenAPI source of truth. Do not edit
  `openapi.yaml` or generated SDK declarations manually.
- Add database schema changes as Alembic migrations. Keep revision identifiers
  at most 32 characters.
- Do not commit credentials, private keys, local environment files, or data
  containing private information.

After changing routes, schemas, examples, or API metadata, regenerate the
OpenAPI snapshot and SDK types:

```bash
.venv/bin/python scripts/generate_openapi.py
cd sdk
npm run generate:types
```

## Checks

Run the Python checks from the repository root:

```bash
.venv/bin/ruff check registry_api tests
ASSET_REGISTRY_TEST_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry \
  .venv/bin/pytest
.venv/bin/python scripts/generate_openapi.py --check
```

Run the SDK checks from `sdk/`:

```bash
npm run check
npm run build
```

The SDK check includes `npm audit --audit-level=high`. Update and commit the
lockfile when a patched transitive dependency is available.

If a check is not applicable or cannot run in your environment, explain that in
the pull request.

## Pull Requests

Describe the problem, the chosen approach, compatibility or migration effects,
and how you tested the change. Update user-facing documentation with behavior
changes and keep generated artifacts in sync.

By contributing, you agree that your contributions are licensed under the
repository's [MIT License](LICENSE).
