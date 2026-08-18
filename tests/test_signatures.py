import pytest
import wallycore as wally

from registry_api.errors import RegistryError
from registry_api.signatures import _bitcoin_signed_message_hash, verify_legacy_deletion_signature


ASSET_ID = "ee909f1b00000000000000000000000000000000000000000000000000000000"
PRIVATE_KEY = (1).to_bytes(wally.EC_PRIVATE_KEY_LEN, "big")
PUBKEY = wally.ec_public_key_from_private_key(PRIVATE_KEY).hex()


def test_legacy_deletion_signature_verifies() -> None:
    verify_legacy_deletion_signature(PUBKEY, deletion_signature(ASSET_ID), ASSET_ID)


def test_invalid_signature_is_rejected() -> None:
    with pytest.raises(RegistryError) as exc_info:
        verify_legacy_deletion_signature(
            "026be637f97bc191c27522577bd6fe284b54404321652fcc4eb62aa0f4cfd6d172",
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==",
            "test",
        )

    assert exc_info.value.error == "invalid_signature"


def deletion_signature(asset_id: str) -> str:
    import base64

    message_hash = _bitcoin_signed_message_hash(f"remove {asset_id} from registry")
    signature = wally.ec_sig_from_bytes(PRIVATE_KEY, message_hash, wally.EC_FLAG_ECDSA)
    return base64.b64encode(signature).decode()
