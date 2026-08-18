# CI Checks

The GitHub Actions workflow in `.github/workflows/ci.yml` runs:

- Python dependency installation.
- Alembic upgrade against PostgreSQL.
- The pytest suite with `ASSET_REGISTRY_TEST_DATABASE_URL` set.
- Generated OpenAPI snapshot verification.

The GitLab pipeline in `.gitlab-ci.yml` uses the project development dependencies to run pytest and the same OpenAPI snapshot check. Database-dependent tests self-skip there unless `ASSET_REGISTRY_TEST_DATABASE_URL` is configured.

The local equivalent is:

```bash
.venv/bin/python -m pip install -e ".[dev]"
ASSET_REGISTRY_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry \
  .venv/bin/alembic upgrade head
ASSET_REGISTRY_TEST_DATABASE_URL=postgresql+psycopg://asset_registry:asset_registry@127.0.0.1:5433/asset_registry \
  .venv/bin/pytest
.venv/bin/python scripts/generate_openapi.py --check
```

When the snapshot check fails after an intentional API change, regenerate it with `.venv/bin/python scripts/generate_openapi.py` and commit the resulting `openapi.yaml`.
