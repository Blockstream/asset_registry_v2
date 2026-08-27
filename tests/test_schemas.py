from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from registry_api.settings import get_settings
from registry_api.schemas import (
    AddAdminAction,
    AssetResponse,
    ContractMetadataResponse,
    IssuerAction,
    LegacyAssetRequest,
    MigrateAssetAction,
    RegisterAssetRequest,
)

ASSET_ID = "AA909F1B00000000000000000000000000000000000000000000000000000000"
PUBKEY = "0382375B3986FEB6F33D96F86C4BC5E09F53D7B3E4EB5B90EECA6D487B7EB40A65"
ACTION_HASH = "a" * 64
ISSUER_ACTION_ADAPTER = TypeAdapter(IssuerAction)


def test_register_asset_request_normalizes_identifiers_and_defaults_domain_verification() -> None:
    request = RegisterAssetRequest.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "initial_issuer_pubkey": PUBKEY,
                "name": "Example Asset",
                "precision": 8,
                "ticker": "EXAMPLE",
                "version": 2,
            },
        }
    )

    assert request.asset_id == ASSET_ID.lower()
    assert request.contract.initial_issuer_pubkey == PUBKEY.lower()
    assert request.domain_verification_method == "http"


def test_register_asset_request_rejects_v2_contract_without_initial_issuer_key() -> None:
    with pytest.raises(ValidationError):
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "name": "Example Asset",
                    "precision": 8,
                    "ticker": "EXAMPLE",
                    "version": 2,
                },
            }
        )


def test_register_asset_request_rejects_v2_contract_without_ticker() -> None:
    with pytest.raises(ValidationError):
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "initial_issuer_pubkey": PUBKEY,
                    "name": "Example Asset",
                    "precision": 8,
                    "version": 2,
                },
            }
        )


def test_register_asset_request_accepts_v2_ticker_at_24_character_limit() -> None:
    request = RegisterAssetRequest.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "initial_issuer_pubkey": PUBKEY,
                "name": "Example Asset",
                "precision": 8,
                "ticker": "A" * 24,
                "version": 2,
            },
        }
    )

    assert request.contract.ticker == "A" * 24


def test_register_asset_request_rejects_v2_ticker_over_24_characters() -> None:
    with pytest.raises(ValidationError):
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "initial_issuer_pubkey": PUBKEY,
                    "name": "Example Asset",
                    "precision": 8,
                    "ticker": "A" * 25,
                    "version": 2,
                },
            }
        )


def test_contract_metadata_response_allows_short_v2_ticker() -> None:
    response = ContractMetadataResponse.model_validate(
        {
            "entity": {"domain": "proof.example.com"},
            "name": "Example Asset",
            "precision": 8,
            "ticker": "A",
            "version": 2,
        }
    )

    assert response.ticker == "A"


def test_contract_metadata_response_omits_absent_optional_contract_fields() -> None:
    response = ContractMetadataResponse.model_validate(
        {
            "entity": {"domain": "proof.example.com"},
            "name": "Untickered legacy asset",
            "precision": 0,
            "version": 0,
            "issuer_pubkey": PUBKEY,
        }
    )

    serialized = response.model_dump(mode="json")

    assert "ticker" not in serialized
    assert "initial_issuer_pubkey" not in serialized
    assert serialized["issuer_pubkey"] == PUBKEY.lower()


def test_register_asset_request_rejects_v2_contract_name_with_only_spaces() -> None:
    with pytest.raises(ValidationError, match="name must not be only whitespace"):
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "initial_issuer_pubkey": PUBKEY,
                    "name": "   ",
                    "precision": 8,
                    "ticker": "EXAMPLE",
                    "version": 2,
                },
            }
        )


def test_legacy_asset_request_allows_contract_name_with_only_spaces() -> None:
    request = LegacyAssetRequest.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "issuer_pubkey": PUBKEY,
                "name": "   ",
                "precision": 0,
                "ticker": "LEGACY",
                "version": 0,
            },
        }
    )

    assert request.contract.name == "   "


@pytest.mark.parametrize(
    "null_field",
    [
        {"ticker": None},
        {"collection": None},
        {"custom_null": None},
        {"nested": {"custom_null": None}},
        {"items": ["value", None]},
    ],
)
def test_legacy_asset_request_rejects_null_contract_values(
    null_field: dict,
) -> None:
    contract = {
        "entity": {"domain": "proof.example.com"},
        "issuer_pubkey": PUBKEY,
        "name": "Legacy Asset",
        "version": 0,
        **null_field,
    }

    with pytest.raises(ValidationError):
        LegacyAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": contract,
            }
        )


def test_legacy_asset_request_rejects_oversized_contract() -> None:
    with pytest.raises(ValidationError, match="legacy contract must be no larger than 4096 bytes"):
        LegacyAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "issuer_pubkey": PUBKEY,
                    "name": "Legacy Asset",
                    "precision": 0,
                    "ticker": "LEGACY",
                    "version": 0,
                    "extra_one": "x" * 2046,
                    "extra_two": "y" * 2046,
                },
            }
        )


def test_legacy_asset_request_uses_configured_contract_size(monkeypatch) -> None:
    monkeypatch.setenv("ASSET_REGISTRY_LEGACY_CONTRACT_MAX_BYTES", "128")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="legacy contract must be no larger than 128 bytes"):
            LegacyAssetRequest.model_validate(
                {
                    "asset_id": ASSET_ID,
                    "contract": {
                        "entity": {"domain": "proof.example.com"},
                        "issuer_pubkey": PUBKEY,
                        "name": "Legacy Asset",
                        "precision": 0,
                        "ticker": "LEGACY",
                        "version": 0,
                    },
                }
            )
    finally:
        get_settings.cache_clear()


def test_register_asset_request_rejects_extra_v2_contract_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "initial_issuer_pubkey": PUBKEY,
                    "image_data_uri": "data:image/png;base64,AAAA",
                    "name": "Example Asset",
                    "precision": 8,
                    "ticker": "EXAMPLE",
                    "version": 2,
                },
            }
        )


def test_register_asset_request_rejects_contract_version_zero() -> None:
    with pytest.raises(ValidationError, match="contract.version = 0"):
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "issuer_pubkey": PUBKEY,
                    "name": "Example Asset",
                    "precision": 0,
                    "ticker": "EXAMPLE",
                    "version": 0,
                },
                "initial_issuer_pubkey": PUBKEY,
            }
        )


def test_register_asset_request_accepts_legacy_key_from_registration_body() -> None:
    request = RegisterAssetRequest.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "name": "Example Asset",
                "precision": 0,
                "ticker": "EXAMPLE",
                "version": 1,
            },
            "initial_issuer_pubkey": PUBKEY,
        }
    )

    assert request.initial_issuer_pubkey == PUBKEY.lower()


def test_register_asset_request_rejects_bad_domain_and_duplicate_tags() -> None:
    with pytest.raises(ValidationError):
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "Proof.example.com"},
                    "initial_issuer_pubkey": PUBKEY,
                    "name": "Example Asset",
                    "precision": 0,
                    "ticker": "EXAMPLE",
                    "version": 2,
                },
                "mutable": {"category_tags": ["stablecoin", "stablecoin"]},
            }
        )


def test_register_asset_request_rejects_bad_trading_venue_with_available_values() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "initial_issuer_pubkey": PUBKEY,
                    "name": "Example Asset",
                    "precision": 0,
                    "ticker": "EXAMPLE",
                    "version": 2,
                },
                "mutable": {
                    "trading_venues": [{"venue": "unknown", "url": "https://example.com"}],
                },
            }
        )

    error = exc_info.value.errors()[0]
    assert error["type"] == "unsupported_trading_venue"
    assert error["ctx"] == {"available_trading_venues": ["bitfinex", "sideswap"]}


def test_register_asset_request_normalizes_category_tags_and_trading_venues() -> None:
    request = RegisterAssetRequest.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "initial_issuer_pubkey": PUBKEY,
                "name": "Example Asset",
                "precision": 0,
                "ticker": "EXAMPLE",
                "version": 2,
            },
            "mutable": {
                "category_tags": ["Bond", "TOKENIZED"],
                "trading_venues": [{"venue": "BITFINEX", "url": "HTTPS://BITFINEX.COM/t/EXAMPLE"}],
            },
        }
    )

    assert request.mutable.category_tags == ["bond", "tokenized"]
    assert request.mutable.trading_venues[0].venue == "bitfinex"
    assert request.mutable.trading_venues[0].url == "https://bitfinex.com/t/EXAMPLE"


def test_register_asset_request_rejects_case_insensitive_duplicate_category_tags() -> None:
    with pytest.raises(ValidationError, match="category_tags must be unique"):
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "initial_issuer_pubkey": PUBKEY,
                    "name": "Example Asset",
                    "precision": 0,
                    "ticker": "EXAMPLE",
                    "version": 2,
                },
                "mutable": {"category_tags": ["Bond", "bond"]},
            }
        )


def test_register_asset_request_rejects_nested_custom_key() -> None:
    with pytest.raises(ValidationError):
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "initial_issuer_pubkey": PUBKEY,
                    "name": "Example Asset",
                    "precision": 0,
                    "ticker": "EXAMPLE",
                    "version": 2,
                },
                "mutable": {"custom": {"nested/key": "value"}},
            }
        )


def test_register_asset_request_rejects_oversized_custom_metadata() -> None:
    with pytest.raises(ValidationError, match="custom metadata value must be no larger"):
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "initial_issuer_pubkey": PUBKEY,
                    "name": "Example Asset",
                    "precision": 0,
                    "ticker": "EXAMPLE",
                    "version": 2,
                },
                "mutable": {"custom": {"image_data_uri": "x" * 2049}},
            }
        )


def test_register_asset_request_rejects_too_many_custom_fields() -> None:
    with pytest.raises(ValidationError, match="more than 32 fields"):
        RegisterAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "initial_issuer_pubkey": PUBKEY,
                    "name": "Example Asset",
                    "precision": 0,
                    "ticker": "EXAMPLE",
                    "version": 2,
                },
                "mutable": {"custom": {f"field_{index}": index for index in range(33)}},
            }
        )


def test_legacy_asset_request_accepts_small_extra_fields() -> None:
    request = LegacyAssetRequest.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "issuer_pubkey": PUBKEY,
                "name": "Legacy Asset",
                "precision": 0,
                "ticker": "LEGACY",
                "version": 0,
                "small_extra": "ok",
            },
            "request_extra": {"ok": True},
        }
    )

    assert request.model_extra == {"request_extra": {"ok": True}}
    assert request.contract.model_extra == {"small_extra": "ok"}


def test_legacy_asset_request_rejects_oversized_extra_field() -> None:
    with pytest.raises(ValidationError, match="legacy contract extra field must be no larger"):
        LegacyAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "issuer_pubkey": PUBKEY,
                    "name": "Legacy Asset",
                    "precision": 0,
                    "ticker": "LEGACY",
                    "version": 0,
                    "image_data_uri": "x" * 2049,
                },
            }
        )


def test_legacy_asset_request_rejects_too_many_extra_fields() -> None:
    with pytest.raises(ValidationError, match="legacy contract must not contain more than 32 extra fields"):
        LegacyAssetRequest.model_validate(
            {
                "asset_id": ASSET_ID,
                "contract": {
                    "entity": {"domain": "proof.example.com"},
                    "issuer_pubkey": PUBKEY,
                    "name": "Legacy Asset",
                    "precision": 0,
                    "ticker": "LEGACY",
                    "version": 0,
                    **{f"extra_{index}": index for index in range(33)},
                },
            }
        )


def test_asset_response_validates_controlled_fields() -> None:
    response = AssetResponse.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "initial_issuer_pubkey": PUBKEY,
                "name": "Example Asset",
                "precision": 0,
                "ticker": "EXAMPLE",
                "version": 2,
            },
            "initial_issuer_pubkey": PUBKEY,
            "initial_issuer_pubkey_source": "contract",
            "current_issuer_pubkey": PUBKEY,
            "mutable": {
                "trading_venues": [{"venue": "sideswap", "url": "HTTPS://SIDESWAP.IO/assets/EXAMPLE"}],
                "category_tags": ["stablecoin"],
            },
            "admin": {"asset_type": "stablecoin"},
            "icon": None,
            "status": "active",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )

    assert response.mutable.trading_venues[0].url == "https://sideswap.io/assets/EXAMPLE"


def test_issuer_action_rejects_old_path_based_payload() -> None:
    with pytest.raises(ValidationError):
        ISSUER_ACTION_ADAPTER.validate_python(
            {
                "signing_context": "liquid-asset-registry-action-v1",
                "asset_id": ASSET_ID,
                "operation": "replace",
                "prev_action_hash": ACTION_HASH,
                "path": "/mutable/category_tags",
                "mutable_schema_version": 1,
                "timestamp": datetime.now(UTC),
                "nonce": "nonce-old-path",
                "value": ["stablecoin"],
            }
        )


def test_issuer_action_accepts_explicit_custom_field_payload() -> None:
    action = ISSUER_ACTION_ADAPTER.validate_python(
        {
            "signing_context": "liquid-asset-registry-action-v1",
            "asset_id": ASSET_ID,
            "operation": "set_custom_field",
            "prev_action_hash": ACTION_HASH,
            "mutable_schema_version": 1,
            "timestamp": datetime.now(UTC),
            "nonce": "nonce-set-custom",
            "custom_key": "isin",
            "value": "US0000000000",
        }
    )

    assert action.operation == "set_custom_field"
    assert action.custom_key == "isin"


def test_issuer_action_rejects_nested_custom_key() -> None:
    with pytest.raises(ValidationError):
        ISSUER_ACTION_ADAPTER.validate_python(
            {
                "signing_context": "liquid-asset-registry-action-v1",
                "asset_id": ASSET_ID,
                "operation": "delete_custom_field",
                "prev_action_hash": ACTION_HASH,
                "mutable_schema_version": 1,
                "timestamp": datetime.now(UTC),
                "nonce": "nonce-delete-custom",
                "custom_key": "nested/key",
            }
        )


def test_issuer_action_rejects_oversized_custom_value() -> None:
    with pytest.raises(ValidationError, match="custom metadata value must be no larger"):
        ISSUER_ACTION_ADAPTER.validate_python(
            {
                "signing_context": "liquid-asset-registry-action-v1",
                "asset_id": ASSET_ID,
                "operation": "set_custom_field",
                "prev_action_hash": ACTION_HASH,
                "mutable_schema_version": 1,
                "timestamp": datetime.now(UTC),
                "nonce": "nonce-large-custom",
                "custom_key": "image_data_uri",
                "value": "x" * 2049,
            }
        )


def test_admin_permissions_accept_migrate_assets() -> None:
    action = AddAdminAction.model_validate(
        {
            "signing_context": "liquid-asset-registry-admin-action-v1",
            "actor_pubkey": PUBKEY,
            "operation": "add_admin",
            "timestamp": datetime.now(UTC),
            "nonce": "nonce-admin-permission",
            "admin_pubkey": PUBKEY,
            "friendly_name": "Migration Admin",
            "permissions": ["migrate_assets"],
        }
    )

    assert action.permissions == ["migrate_assets"]


def test_migrate_asset_admin_action_validates_asset_id() -> None:
    action = MigrateAssetAction.model_validate(
        {
            "signing_context": "liquid-asset-registry-admin-action-v1",
            "actor_pubkey": PUBKEY,
            "operation": "migrate_asset",
            "timestamp": datetime.now(UTC),
            "nonce": "nonce-migrate-asset",
            "asset_id": ASSET_ID,
        }
    )

    assert action.asset_id == ASSET_ID.lower()
