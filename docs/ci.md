# CI Checks

The GitHub Actions workflow in `.github/workflows/ci.yml` runs:

- Python dependency installation.
- Alembic upgrade against PostgreSQL.
- The pytest suite with `ASSET_REGISTRY_TEST_DATABASE_URL` set.
- Generated OpenAPI snapshot verification.

The GitLab test job in `.gitlab-ci.yml` installs the project development dependencies
and starts a fresh `postgres:16` service. Both `ASSET_REGISTRY_DATABASE_URL` (used by
Alembic and the application) and `ASSET_REGISTRY_TEST_DATABASE_URL` (used by the test
fixtures) point to the service's `asset_registry_test` database at `postgres:5432`.
The job runs `alembic upgrade head` before pytest, so database-dependent tests,
including the registry-version triggers and commit-ordering checks, run in every
test job. Migration or database connection failures fail the job. The pytest suite
also verifies the tracked OpenAPI snapshot.

For local runs, create the dedicated test database if it does not already exist:
`docker compose exec postgres createdb -U asset_registry asset_registry_test`.
Then run:

```bash
.venv/bin/python -m pip install -e ".[dev]"
ASSET_REGISTRY_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry_test \
  .venv/bin/alembic upgrade head
ASSET_REGISTRY_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry_test \
  ASSET_REGISTRY_TEST_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry_test \
  .venv/bin/pytest
.venv/bin/python scripts/generate_openapi.py --check
```

When the snapshot check fails after an intentional API change, regenerate it with `.venv/bin/python scripts/generate_openapi.py` and commit the resulting `openapi.yaml`.
