import os
from dotenv import load_dotenv

load_dotenv()


def get_signer_token() -> str:
    token = os.environ.get("SIGNER_TOKEN")
    if not token:
        raise RuntimeError("SIGNER_TOKEN environment variable is required")
    return token


def get_private_key_bytes() -> bytes:
    hex_key = os.environ.get("PRIVATE_KEY_HEX")
    if not hex_key:
        raise RuntimeError("PRIVATE_KEY_HEX environment variable is required")
    try:
        return bytes.fromhex(hex_key)
    except ValueError:
        raise RuntimeError("PRIVATE_KEY_HEX must be a valid hex string")
