# Configuration

Configuration is read from environment variables with the `ASSET_REGISTRY_` prefix.

| Variable | Default | Notes |
| --- | --- | --- |
| `ASSET_REGISTRY_APP_NAME` | `Liquid Asset Registry` | FastAPI application title. |
| `ASSET_REGISTRY_ENVIRONMENT` | `development` | Environment label for operators. |
| `ASSET_REGISTRY_DEBUG` | `false` | Enables FastAPI debug mode. |
| `ASSET_REGISTRY_LOG_LEVEL` | `INFO` | Root logging level. Logs are emitted as JSON. |
| `ASSET_REGISTRY_HOST` | `0.0.0.0` | Server bind host when used by launch scripts. |
| `ASSET_REGISTRY_PORT` | `8000` | Server bind port when used by launch scripts. |
| `ASSET_REGISTRY_DATABASE_URL` | PostgreSQL compose URL | SQLAlchemy database URL. |
| `ASSET_REGISTRY_NETWORK` | `liquid` | `liquid` or `liquidtestnet`; selects default Esplora URL. |
| `ASSET_REGISTRY_ESPLORA_URL` | network default | Esplora-compatible Liquid API URL. |
| `ASSET_REGISTRY_HTTP_TIMEOUT_SECONDS` | `10.0` | Timeout for HTTP proof and chain clients. HTTP proof connection attempts share one timeout budget across validated addresses. |
| `ASSET_REGISTRY_MAX_REQUEST_BODY_BYTES` | `1048576` | App-level maximum HTTP request body size. Set to `0` to disable only when `ASSET_REGISTRY_MAX_JSON_DEPTH=0`. Deployments should also enforce proxy body limits. |
| `ASSET_REGISTRY_MAX_JSON_DEPTH` | `100` | Maximum nesting depth for JSON request bodies. Set to `0` to disable the application-level depth guard. Requires a positive request body size limit when enabled. |
| `ASSET_REGISTRY_DNS_OVER_HTTPS_URL` | `https://dns.google/resolve` | DNS-over-HTTPS resolver URL. |
| `ASSET_REGISTRY_ENFORCE_CHAIN_VERIFICATION` | `true` | Require issuance commitment verification. |
| `ASSET_REGISTRY_ENFORCE_DOMAIN_VERIFICATION` | `true` | Require HTTP/DNS domain proof verification. |
| `ASSET_REGISTRY_REGISTRATION_RATE_LIMIT` | `30` | Maximum registration and migration requests accepted per client IP in each rate-limit window. Set to `0` to disable. |
| `ASSET_REGISTRY_REGISTRATION_RATE_LIMIT_WINDOW_SECONDS` | `60.0` | Sliding-window duration for per-client registration and migration limits. |
| `ASSET_REGISTRY_DOMAIN_FETCH_FAILURE_COOLDOWN_SECONDS` | `30.0` | Cooldown after a failed proof fetch for the same domain. Set to `0` to disable. |
| `ASSET_REGISTRY_DOMAIN_FETCH_QUOTA` | `20` | Maximum HTTP or DNS proof fetches reserved per domain in each quota window. Set to `0` to disable. |
| `ASSET_REGISTRY_DOMAIN_FETCH_QUOTA_WINDOW_SECONDS` | `60.0` | Window duration for per-domain proof-fetch quotas. |
| `ASSET_REGISTRY_MAX_CONCURRENT_PROOF_FETCHES` | `16` | Maximum proof-resolution/fetch workers per process. Set to `0` to disable. |
| `ASSET_REGISTRY_GENESIS_ADMIN_PUBKEY` | unset | Optional compressed secp256k1 pubkey used to bootstrap the first active `root` admin when no admins exist. |
| `ASSET_REGISTRY_LEGACY_BASE_URL` | unset | Base URL for the original registry. |
| `ASSET_REGISTRY_LEGACY_SHADOW_WRITE` | `false` | Forward supported legacy writes to the original registry after local persistence. |
| `ASSET_REGISTRY_LEGACY_COMPARE_RESPONSES` | `true` | Compare legacy/local responses where supported. |
| `ASSET_REGISTRY_LEGACY_TIMEOUT_SECONDS` | `10.0` | Legacy client timeout. |
| `ASSET_REGISTRY_LEGACY_FAILURE_SANITY_DELAY_SECONDS` | `5.0` | Delay before legacy failure sanity lookup. |
| `ASSET_REGISTRY_LEGACY_CONTRACT_MAX_BYTES` | `4096` | Maximum canonical serialized legacy `contract` object size. Raise temporarily if needed while shadowing the original registry. |

Every response includes an `X-Request-ID` header. If the client supplies `X-Request-ID`, the service preserves it; otherwise it generates a UUID.

## Reverse proxy client IPs

Registration rate limits use the client address resolved by Uvicorn in `request.client`. The container starts Uvicorn with proxy-header handling enabled. Uvicorn accepts `X-Forwarded-For` only when the immediate socket peer is trusted by its `FORWARDED_ALLOW_IPS` setting, which defaults to `127.0.0.1`. If the reverse proxy connects from another address, set `FORWARDED_ALLOW_IPS` to a comma-separated list containing only the proxy IPs or CIDRs. Do not use `*` when untrusted clients can connect to Uvicorn directly.

Request logs expose `client` as the resolved address without logging the forwarded chain. For deployment diagnostics, `forwarded_for_present` reports whether the header was present and `client_forwarded_match` reports whether the resolved address appeared in that chain. Proxied requests normally show both as `true`; `client_forwarded_match=false` indicates that the socket peer was retained or that the forwarded header had an unexpected format. These fields are diagnostic only—Uvicorn's trusted-peer configuration remains the security boundary.

Production deployments should also restrict API container egress. HTTP domain proof verification fetches issuer-controlled domains and rejects non-public DNS targets in application code, but network policy is still the stronger control.

For public DNS names, HTTP proof verification ignores `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` and connects directly to the validated IP addresses. This is required to ensure the peer is one of the addresses checked by the service, rather than an address resolved later by a proxy. The service therefore needs direct DNS and outbound HTTPS access for clearnet proofs. `.onion` proofs cannot use public-IP validation and retain HTTPX's environment-proxy behavior so operators can route them through a Tor-capable proxy.
