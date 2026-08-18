import hashlib
import ecdsa
from ecdsa import SECP256k1

# secp256k1 curve order half (for low-S check)
_HALF_ORDER = 0x3FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DBFE92F1A9F2A0746


def _btl_encode(length: int) -> bytes:
    """Encode an integer using Bitcoin's BTL variable-length format."""
    if length < 0xFC:
        return bytes([length])
    elif length <= 0xFFFF:
        return bytes([0xFD]) + length.to_bytes(2, "little")
    elif length <= 0xFFFFFFFF:
        return bytes([0xFE]) + length.to_bytes(4, "little")
    else:
        return bytes([0xFF]) + length.to_bytes(8, "little")


def _bitcoin_message_magic(message: bytes) -> bytes:
    """Construct the Bitcoin signed message prefix.

    Format: BTL(24) + "Bitcoin Signed Message:\n" + BTL(msg_len) + msg
    """
    magic = b"Bitcoin Signed Message:\n"
    return _btl_encode(len(magic)) + magic + _btl_encode(len(message)) + message


def sign_bytes(message: bytes, private_key_bytes: bytes) -> str:
    """Sign message bytes using the Bitcoin signed message format."""
    privkey = ecdsa.SigningKey.from_string(private_key_bytes, curve=SECP256k1)

    prefixed = _bitcoin_message_magic(message)
    digest = hashlib.sha256(hashlib.sha256(prefixed).digest()).digest()
    raw = privkey.sign_digest(digest, sigencode=ecdsa.util.sigencode_string)

    s_int = int.from_bytes(raw[32:], "big")
    rec_id = 27 if s_int <= _HALF_ORDER else 28

    return bytes([rec_id]).hex() + raw.hex()


def sign(message: str, private_key_bytes: bytes) -> str:
    """Sign a UTF-8 message using the Bitcoin signed message format.

    Returns recovery-byte + R‖S as a hex string (e.g. "1fabcd...").
    """
    return sign_bytes(message.encode("utf-8"), private_key_bytes)
