import base64
import binascii

import wallycore as wally

from registry_api.errors import ErrorCode, RegistryError


def verify_legacy_deletion_signature(pubkey_hex: str, signature_base64: str, asset_id: str) -> None:
    signature = _decode_compact_signature(signature_base64)
    pubkey = _decode_compressed_pubkey(pubkey_hex)
    message_hash = _bitcoin_signed_message_hash(f"remove {asset_id} from registry")
    if not _verify_ecdsa(pubkey, signature, message_hash):
        raise RegistryError(ErrorCode.INVALID_SIGNATURE, "deletion signature verification failed", status_code=401)


def verify_canonical_payload_signature(
    pubkey_hex: str,
    signature_base64: str,
    payload: bytes,
    *,
    failure_message: str = "issuer action signature verification failed",
) -> None:
    signature = _decode_compact_signature(signature_base64)
    pubkey = _decode_compressed_pubkey(pubkey_hex)
    message_hash = _bitcoin_signed_message_hash(payload.decode("utf-8"))
    if not _verify_ecdsa(pubkey, signature, message_hash):
        raise RegistryError(ErrorCode.INVALID_SIGNATURE, failure_message, status_code=401)


def validate_signature_encoding(signature_base64: str) -> None:
    """Reject malformed credentials before parsing a signed request body."""
    _decode_compact_signature(signature_base64)


def _decode_compact_signature(signature_base64: str) -> bytes:
    try:
        signature = base64.b64decode(signature_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RegistryError(ErrorCode.INVALID_SIGNATURE, "signature must be base64 encoded", status_code=401) from exc

    if len(signature) == 65:
        signature = signature[1:]
    if len(signature) != 64:
        raise RegistryError(
            ErrorCode.INVALID_SIGNATURE,
            "signature must be a compact secp256k1 signature",
            status_code=401,
        )
    return signature


def _bitcoin_signed_message_hash(message: str) -> bytes:
    return wally.format_bitcoin_message(message.encode("utf-8"), wally.BITCOIN_MESSAGE_FLAG_HASH)


def _decode_compressed_pubkey(pubkey_hex: str) -> bytes:
    try:
        pubkey = bytes.fromhex(pubkey_hex)
        wally.ec_public_key_verify(pubkey)
    except ValueError as exc:
        raise RegistryError(ErrorCode.INVALID_PUBKEY, "issuer public key must be compressed secp256k1") from exc
    if len(pubkey) != wally.EC_PUBLIC_KEY_LEN:
        raise RegistryError(ErrorCode.INVALID_PUBKEY, "issuer public key must be compressed secp256k1")
    return pubkey


def _verify_ecdsa(pubkey: bytes, signature: bytes, message_hash: bytes) -> bool:
    try:
        normalized_signature = wally.ec_sig_normalize(signature)
        wally.ec_sig_verify(pubkey, message_hash, wally.EC_FLAG_ECDSA, normalized_signature)
    except ValueError:
        return False
    return True
