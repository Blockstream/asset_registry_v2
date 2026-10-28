#!/usr/bin/env python3
from __future__ import annotations

import json
import secrets
import sys

import wallycore as wally


def generate_keypair() -> dict[str, str]:
    while True:
        private_key = secrets.token_bytes(wally.EC_PRIVATE_KEY_LEN)
        try:
            wally.ec_private_key_verify(private_key)
        except ValueError:
            continue
        break

    public_key = wally.ec_public_key_from_private_key(private_key)
    return {
        "private_key": private_key.hex(),
        "public_key": public_key.hex(),
    }


def main() -> None:
    print(
        "Keep private_key secret. Share only public_key with the registry administrator.",
        file=sys.stderr,
    )
    print(json.dumps(generate_keypair(), indent=2))


if __name__ == "__main__":
    main()
