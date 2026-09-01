import json
import re

import wallycore as wally

from scripts.generate_admin_keypair import generate_keypair, main


def test_generate_keypair_returns_matching_hex_keys() -> None:
    keypair = generate_keypair()

    private_key = keypair["private_key"]
    public_key = keypair["public_key"]

    assert re.fullmatch(r"[0-9a-f]{64}", private_key)
    assert re.fullmatch(r"(?:02|03)[0-9a-f]{64}", public_key)
    wally.ec_private_key_verify(bytes.fromhex(private_key))
    assert wally.ec_public_key_from_private_key(bytes.fromhex(private_key)).hex() == public_key


def test_main_prints_json_and_private_key_warning(capsys) -> None:
    main()

    captured = capsys.readouterr()
    keypair = json.loads(captured.out)

    assert set(keypair) == {"private_key", "public_key"}
    assert "Keep private_key secret" in captured.err
