import pytest
from pydantic import ValidationError

from registry_api.settings import Settings


def test_network_defaults_to_liquid_esplora() -> None:
    settings = Settings()

    assert settings.network == "liquid"
    assert settings.esplora_url == "https://blockstream.info/liquid/api"


def test_network_can_select_liquid_testnet_esplora() -> None:
    settings = Settings(network="liquidtestnet")

    assert settings.esplora_url == "https://blockstream.info/liquidtestnet/api"


def test_explicit_esplora_url_overrides_network_default() -> None:
    settings = Settings(network="liquidtestnet", esplora_url="https://example.com/api")

    assert settings.esplora_url == "https://example.com/api"


def test_legacy_failure_sanity_delay_defaults_to_five_seconds() -> None:
    settings = Settings()

    assert settings.legacy_failure_sanity_delay_seconds == 5.0


def test_legacy_contract_max_bytes_defaults_to_4096() -> None:
    settings = Settings()

    assert settings.legacy_contract_max_bytes == 4096


def test_max_json_depth_defaults_to_100() -> None:
    settings = Settings()

    assert settings.max_json_depth == 100


def test_body_size_limit_can_be_disabled_when_json_depth_guard_is_disabled() -> None:
    settings = Settings(max_request_body_bytes=0, max_json_depth=0)

    assert settings.max_request_body_bytes == 0
    assert settings.max_json_depth == 0


def test_json_depth_guard_requires_body_size_limit() -> None:
    with pytest.raises(ValidationError, match="max_request_body_bytes"):
        Settings(max_request_body_bytes=0)
