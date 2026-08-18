from registry_api.legacy_response import legacy_response_from_asset
from registry_api.models import Asset


TESTNET_ASSET_ID = "92ac106ea13e65fe1ccae10ca69d6bd14147bb445f4c6119b8db5d027c543d15"
COMPRESSED_PUBKEY = "03d8bb0680c89a19042a4c30f52ebb334dc075bcd53191e4de3885faa0eb6437db"
UNCOMPRESSED_PUBKEY = (
    "04d8bb0680c89a19042a4c30f52ebb334dc075bcd53191e4de3885faa0eb6437db"
    "56a4e71286ab497274361ef558d4c69eaacdb3eacbe7a469f25de95636db4117"
)


def test_testnet_legacy_response_restores_uncompressed_contract_pubkey_for_hash_verification() -> None:
    """Preserve original v1 contract bytes for four legacy testnet assets.

    The legacy response shim exists only to keep these assets compatible with
    contract hash verification, since the asset_id is derived from the contract
    hash and the original contracts used an uncompressed issuer_pubkey. No mainnet
    assets use uncompressed public keys as of the date of this commit.
    """
    asset = Asset(asset_id=TESTNET_ASSET_ID)
    registered_response = {
        "asset_id": TESTNET_ASSET_ID,
        "contract": {
            "entity": {"domain": "api.dev.iodevnet.com"},
            "issuer_pubkey": COMPRESSED_PUBKEY,
            "name": "BrianCoin",
            "precision": 8,
            "ticker": "BRI",
            "version": 0,
        },
        "issuer_pubkey": COMPRESSED_PUBKEY,
        "name": "BrianCoin",
        "precision": 8,
        "ticker": "BRI",
        "version": 0,
    }

    response = legacy_response_from_asset(asset, registered_response)

    assert response["contract"]["issuer_pubkey"] == UNCOMPRESSED_PUBKEY
    assert response["issuer_pubkey"] == UNCOMPRESSED_PUBKEY
