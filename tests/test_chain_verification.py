from pathlib import Path

import httpx
import pytest
import wallycore as wally

from registry_api.chain import (
    EsploraChainVerifier,
    IssuanceCommitment,
    derive_asset_id,
    generate_asset_entropy,
    parse_issuance_input,
)
from registry_api.errors import RegistryError


ISSUANCE_FIXTURE = Path("tests/fixtures/liquid-taproot-issuance.hex")
ISSUANCE_TXID = "d81572734b18580c8371c0ffe539c443642233035766b09bb4012bfdaed44392"
ISSUANCE_PREVOUT_TXID = (
    "612c215ba1408ebeddb3b27b3be77f63672723c55c23d60a06dd26da2dc11f39"
)
ISSUANCE_PREVOUT_VOUT = 0
ISSUANCE_CONTRACT_HASH = (
    "ac7410a3d470963a02f464b0d1b5de9b7bd082247e3b76700f259c98ffcae2e6"
)
ISSUED_ASSET_ID = "611a55d64ec7b5f94a6ce33c997fff9dcd6a1d435fc9478977213afc9791e2ff"


def test_asset_entropy_and_asset_id_match_elements_vectors() -> None:
    contract_hash_hex = (
        "0000000000000000000000000000000000000000000000000000000000000000"
    )
    entropy = generate_asset_entropy(
        "05a047c98e82a848dee94efcf32462b065198bebf2404d201ba2e06db30b28f4",
        0,
        contract_hash_hex,
    )

    assert (
        entropy[::-1].hex()
        == "746f447f691323502cad2ef646f932613d37a83aeaa2133185b316648df4b70a"
    )
    assert derive_asset_id(
        "05a047c98e82a848dee94efcf32462b065198bebf2404d201ba2e06db30b28f4",
        0,
        contract_hash_hex,
    ) == ("dcd60818d863b5c026c40b2bc3ba6fdaf5018bcc8606c18adf7db4da0bcd8533")


def test_parse_issuance_input_reads_prevout_and_contract_hash_from_fixture() -> None:
    parsed = parse_issuance_input(ISSUANCE_FIXTURE.read_text(), 0)

    assert parsed.previous_txid == ISSUANCE_PREVOUT_TXID
    assert parsed.previous_vout == ISSUANCE_PREVOUT_VOUT
    assert parsed.asset_entropy == ISSUANCE_CONTRACT_HASH


def test_parse_issuance_input_supports_liquid_taproot_issuance() -> None:
    tx_hex = ISSUANCE_FIXTURE.read_text()
    tx = wally.tx_from_hex(tx_hex.strip(), wally.WALLY_TX_FLAG_USE_ELEMENTS)

    assert (
        wally.tx_get_txid(tx)[::-1].hex()
        == "d81572734b18580c8371c0ffe539c443642233035766b09bb4012bfdaed44392"
    )
    assert wally.tx_get_output_script(tx, 1).hex() == (
        "51200f374b6a73e22623c90176e75bac23dcd1012ad15e527859cd306f7fc11d7f6a"
    )

    parsed = parse_issuance_input(tx_hex, 0)

    assert (
        parsed.previous_txid
        == "612c215ba1408ebeddb3b27b3be77f63672723c55c23d60a06dd26da2dc11f39"
    )
    assert parsed.previous_vout == 0
    assert (
        parsed.asset_entropy
        == "ac7410a3d470963a02f464b0d1b5de9b7bd082247e3b76700f259c98ffcae2e6"
    )
    assert derive_asset_id(
        parsed.previous_txid, parsed.previous_vout, parsed.asset_entropy
    ) == ("611a55d64ec7b5f94a6ce33c997fff9dcd6a1d435fc9478977213afc9791e2ff")


@pytest.mark.parametrize("tx_hex", ["not hex", "02000000"])
def test_parse_issuance_input_rejects_invalid_transaction(tx_hex: str) -> None:
    with pytest.raises(RegistryError) as exc_info:
        parse_issuance_input(tx_hex, 0)

    assert exc_info.value.error == "chain_verification_failed"
    assert exc_info.value.message == "invalid issuance transaction"


def test_parse_issuance_input_rejects_missing_input() -> None:
    tx_hex = ISSUANCE_FIXTURE.read_text()

    with pytest.raises(RegistryError) as exc_info:
        parse_issuance_input(tx_hex, 1)

    assert exc_info.value.error == "chain_verification_failed"
    assert exc_info.value.message == "issuance transaction missing input"


def test_parse_issuance_input_rejects_input_without_issuance() -> None:
    tx_hex = ISSUANCE_FIXTURE.read_text()
    tx = wally.tx_from_hex(tx_hex.strip(), wally.WALLY_TX_FLAG_USE_ELEMENTS)
    wally.tx_set_input_issuance_amount(tx, 0, None)
    wally.tx_set_input_inflation_keys(tx, 0, None)
    tx_hex_without_issuance = wally.tx_to_hex(
        tx, wally.WALLY_TX_FLAG_USE_ELEMENTS | wally.WALLY_TX_FLAG_USE_WITNESS
    )

    with pytest.raises(RegistryError) as exc_info:
        parse_issuance_input(tx_hex_without_issuance, 0)

    assert exc_info.value.error == "chain_verification_failed"
    assert exc_info.value.message == "input has no issuance"


def test_esplora_chain_verifier_accepts_fixture_data() -> None:
    tx_hex = ISSUANCE_FIXTURE.read_text()

    def get_json(path: str) -> dict:
        if path == f"/asset/{ISSUED_ASSET_ID}":
            return {
                "issuance_txin": {"txid": ISSUANCE_TXID, "vin": 0},
                "issuance_prevout": {
                    "txid": ISSUANCE_PREVOUT_TXID,
                    "vout": ISSUANCE_PREVOUT_VOUT,
                },
            }
        if path == f"/tx/{ISSUANCE_TXID}/status":
            return {"confirmed": True}
        raise AssertionError(path)

    def get_text(path: str) -> str:
        assert path == f"/tx/{ISSUANCE_TXID}/hex"
        return tx_hex

    verifier = EsploraChainVerifier(
        "https://example.invalid", get_json=get_json, get_text=get_text
    )

    verifier.verify_issuance_commitment(
        IssuanceCommitment(
            asset_id=ISSUED_ASSET_ID, contract_hash=ISSUANCE_CONTRACT_HASH
        )
    )


def test_esplora_chain_verifier_rejects_unconfirmed_issuance() -> None:
    def get_json(path: str) -> dict:
        if path == f"/asset/{ISSUED_ASSET_ID}":
            return {
                "issuance_txin": {"txid": ISSUANCE_TXID, "vin": 0},
                "issuance_prevout": {
                    "txid": ISSUANCE_PREVOUT_TXID,
                    "vout": ISSUANCE_PREVOUT_VOUT,
                },
            }
        return {"confirmed": False}

    verifier = EsploraChainVerifier(
        "https://example.invalid", get_json=get_json, get_text=lambda _path: ""
    )

    with pytest.raises(RegistryError) as exc_info:
        verifier.verify_issuance_commitment(
            IssuanceCommitment(
                asset_id=ISSUED_ASSET_ID, contract_hash=ISSUANCE_CONTRACT_HASH
            )
        )

    assert exc_info.value.error == "chain_verification_failed"


def test_esplora_404_asset_is_chain_verification_failure(monkeypatch) -> None:
    original_client = httpx.Client

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    verifier = EsploraChainVerifier("https://example.invalid")

    with pytest.raises(RegistryError) as exc_info:
        verifier.verify_issuance_commitment(
            IssuanceCommitment(asset_id="00" * 32, contract_hash="11" * 32)
        )

    assert exc_info.value.error == "chain_verification_failed"
    assert exc_info.value.message == "asset not found"


def test_esplora_404_transaction_hex_is_chain_verification_failure(monkeypatch) -> None:
    original_client = httpx.Client
    txid = "22" * 32

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith(f"/asset/{'00' * 32}"):
            return httpx.Response(
                200,
                json={
                    "issuance_txin": {"txid": txid, "vin": 0},
                    "issuance_prevout": {"txid": "33" * 32, "vout": 0},
                },
            )
        return httpx.Response(404, text="not found")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    verifier = EsploraChainVerifier("https://example.invalid")

    with pytest.raises(RegistryError) as exc_info:
        verifier.verify_issuance_commitment(
            IssuanceCommitment(asset_id="00" * 32, contract_hash="11" * 32)
        )

    assert exc_info.value.error == "chain_verification_failed"
    assert exc_info.value.message == "issuance transaction not found"


def test_esplora_connection_error_is_chain_verifier_unreachable(monkeypatch) -> None:
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    verifier = EsploraChainVerifier("https://example.invalid")

    with pytest.raises(RegistryError) as exc_info:
        verifier.verify_issuance_commitment(
            IssuanceCommitment(asset_id="00" * 32, contract_hash="11" * 32)
        )

    assert exc_info.value.error == "chain_verifier_unreachable"
    assert exc_info.value.status_code == 503


def test_esplora_non_404_http_error_is_service_error(monkeypatch) -> None:
    original_client = httpx.Client

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream error")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    verifier = EsploraChainVerifier("https://example.invalid")

    with pytest.raises(RegistryError) as exc_info:
        verifier.verify_issuance_commitment(
            IssuanceCommitment(asset_id="00" * 32, contract_hash="11" * 32)
        )

    assert exc_info.value.error == "chain_verifier_error"
    assert exc_info.value.status_code == 503
    assert "not a problem with the submitted asset" in exc_info.value.message
