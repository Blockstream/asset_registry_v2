from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ESPLORA_URLS = {
    "liquid": "https://blockstream.info/liquid/api",
    "liquidtestnet": "https://blockstream.info/liquidtestnet/api",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASSET_REGISTRY_", env_file=".env", extra="ignore"
    )

    app_name: str = "Liquid Asset Registry"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = Field(
        default="postgresql+psycopg://asset_registry:asset_registry@postgres:5432/asset_registry"
    )
    network: str = "liquid"
    esplora_url: str | None = None
    http_timeout_seconds: float = 10.0
    max_request_body_bytes: int = Field(default=1_048_576, ge=0)
    max_json_depth: int = Field(default=100, ge=0)
    dns_over_https_url: str = "https://dns.google/resolve"
    enforce_chain_verification: bool = True
    enforce_domain_verification: bool = True
    registration_rate_limit: int = Field(default=30, ge=0)
    registration_rate_limit_window_seconds: float = Field(default=60.0, gt=0)
    domain_fetch_failure_cooldown_seconds: float = Field(default=30.0, ge=0)
    domain_fetch_quota: int = Field(default=20, ge=0)
    domain_fetch_quota_window_seconds: float = Field(default=60.0, gt=0)
    max_concurrent_proof_fetches: int = Field(default=16, ge=0)
    legacy_base_url: str | None = None
    legacy_shadow_write: bool = False
    legacy_compare_responses: bool = True
    legacy_timeout_seconds: float = 10.0
    legacy_failure_sanity_delay_seconds: float = 5.0
    legacy_contract_max_bytes: int = Field(default=4096, gt=0)
    genesis_admin_pubkey: str | None = None

    @model_validator(mode="after")
    def apply_network_defaults(self) -> "Settings":
        if self.network not in ESPLORA_URLS:
            raise ValueError("network must be one of: liquid, liquidtestnet")
        if self.esplora_url is None:
            self.esplora_url = ESPLORA_URLS[self.network]
        if self.max_json_depth > 0 and self.max_request_body_bytes <= 0:
            raise ValueError(
                "max_request_body_bytes must be greater than 0 when max_json_depth is enabled"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
