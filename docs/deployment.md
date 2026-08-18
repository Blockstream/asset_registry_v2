# Deployment Notes

Run migrations before serving traffic:

```bash
alembic upgrade head
```

Recommended production settings:

- Set `ASSET_REGISTRY_ENVIRONMENT=production`.
- Set `ASSET_REGISTRY_LOG_LEVEL=INFO`.
- Set `ASSET_REGISTRY_DATABASE_URL` to the production PostgreSQL URL.
- Set `ASSET_REGISTRY_ENFORCE_CHAIN_VERIFICATION=true`.
- Set `ASSET_REGISTRY_ENFORCE_DOMAIN_VERIFICATION=true`.
- Set `ASSET_REGISTRY_MAX_REQUEST_BODY_BYTES` to the largest expected registry payload size and enforce a matching or smaller reverse-proxy body limit.
- When deploying behind a reverse proxy that does not connect over loopback, set Uvicorn's `FORWARDED_ALLOW_IPS` environment variable to only that proxy's IP networks and configure the proxy to overwrite `X-Forwarded-For`. Its default, `127.0.0.1`, needs no change for a loopback proxy. Never use `*` when untrusted clients can connect to Uvicorn directly.
- Set `ASSET_REGISTRY_GENESIS_ADMIN_PUBKEY` for first deployment so the first signed admin action can bootstrap from a configured root admin key. Remove or rotate that root admin through signed admin lifecycle actions after operational admins are established.
- Set `ASSET_REGISTRY_LEGACY_SHADOW_WRITE=true` and related legacy variables only during migration/write-gate rollout.
- Restrict API container egress where possible. HTTP domain proof verification fetches issuer-controlled domains, so network policy should prevent access to internal services.
- Allow direct DNS and outbound HTTPS access for clearnet HTTP proofs. These requests deliberately ignore environment proxy variables so the connection can be pinned to the IP addresses validated by the service. If direct clearnet egress is prohibited, enforce equivalent destination validation in an approved egress gateway before enabling HTTP domain verification.
- If `.onion` domain proofs are supported, configure a Tor-capable proxy through HTTPX's standard environment variables. Onion proofs are the only HTTP proof requests that retain environment-proxy handling.

Operational behavior:

- Logs are JSON objects written to stdout.
- Each request has an `X-Request-ID` response header and matching `request_id` log field.
- Request logs include Uvicorn's resolved `client` address plus `forwarded_for_present` and `client_forwarded_match` booleans for checking proxy resolution without recording the forwarded chain.
- Database writes are transaction-scoped in service-layer functions. Failed writes roll back before errors are returned.
- The SQLAlchemy engine uses `pool_pre_ping=True` to avoid stale pooled connections.
- The current retry policy is conservative: the service does not automatically replay write transactions after database errors because issuer action nonce handling and legacy write-gate forwarding must remain single-application operations.
- `POST /v2/assets/{asset_id}/migrate` requires a signed admin action with `migrate_assets` or `root` permission.

Health and docs:

- `GET /health` returns process health.
- `GET /docs` exposes FastAPI Swagger UI.
- `GET /openapi.json` exposes the generated OpenAPI schema.
