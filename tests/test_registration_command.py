from registry_api.registration_command import command_from_legacy_registration
from registry_api.schemas import LegacyAssetRequest


PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"


def test_legacy_registration_command_preserves_missing_ticker_as_none() -> None:
    request = LegacyAssetRequest.model_validate(
        {
            "asset_id": "aa909f1b00000000000000000000000000000000000000000000000000000000",
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "issuer_pubkey": PUBKEY,
                "name": "Untickered Legacy Asset",
                "precision": 0,
                "version": 0,
            },
        }
    )

    command = command_from_legacy_registration(request)

    assert command.ticker is None
    assert "ticker" not in command.contract


def test_legacy_registration_command_preserves_omitted_default_field() -> None:
    request = LegacyAssetRequest.model_validate(
        {
            "asset_id": "ac909f1b00000000000000000000000000000000000000000000000000000000",
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "issuer_pubkey": PUBKEY,
                "name": "Default precision asset",
                "version": 0,
            },
        }
    )

    command = command_from_legacy_registration(request)

    assert command.precision == 0
    assert "precision" not in command.contract
