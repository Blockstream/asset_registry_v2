# Local Fixtures

Start PostgreSQL:

```bash
docker compose up -d postgres
```

Apply migrations:

```bash
ASSET_REGISTRY_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry \
  .venv/bin/alembic upgrade head
```

Run the full test suite against PostgreSQL:

```bash
ASSET_REGISTRY_TEST_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry \
  .venv/bin/pytest
```

Tests that require `ASSET_REGISTRY_TEST_DATABASE_URL` skip cleanly when the variable is not set. The PostgreSQL-backed fixtures clean the registry tables before and after each test.

Run the API locally:

```bash
ASSET_REGISTRY_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry \
  .venv/bin/uvicorn registry_api.main:app --reload
```
