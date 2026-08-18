import base64

import pytest
import wallycore as wally

from registry_api.domain_verification import (
    PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT,
    DomainProof,
    expected_dns_proof,
    expected_http_proof,
    expected_pubkey_bound_domain_proof,
    normalized_contract_signature_payload,
    parse_pubkey_bound_domain_proof,
    proof_url,
    verify_domain_proof,
    verify_pubkey_bound_domain_signature,
)
from registry_api.errors import RegistryError
from registry_api.schemas import ContractMetadata
from registry_api.signatures import _bitcoin_signed_message_hash

ASSET_ID = "aa909f1b00000000000000000000000000000000000000000000000000000000"
PRIVATE_KEY = (1).to_bytes(wally.EC_PRIVATE_KEY_LEN, "big")
PUBKEY = wally.ec_public_key_from_private_key(PRIVATE_KEY).hex()


def test_http_domain_verification_uses_expected_well_known_url_and_body() -> None:
    seen_urls = []

    def fetch_text(url: str) -> str:
        seen_urls.append(url)
        return expected_http_proof("proof.example.com", ASSET_ID)

    verify_domain_proof(DomainProof("proof.example.com", ASSET_ID), "http", fetch_text=fetch_text)

    assert seen_urls == [proof_url("proof.example.com", ASSET_ID)]


def test_http_domain_verification_rejects_mismatched_body() -> None:
    with pytest.raises(RegistryError) as exc_info:
        verify_domain_proof(DomainProof("proof.example.com", ASSET_ID), "http", fetch_text=lambda _url: "wrong")

    assert exc_info.value.error == "domain_verification_failed"


def test_dns_domain_verification_accepts_matching_txt_record() -> None:
    records = ["unrelated", expected_dns_proof(ASSET_ID, "TEST")]

    verify_domain_proof(DomainProof("proof.example.com", ASSET_ID, "TEST"), "dns", resolve_txt=lambda _domain: records)


def test_domain_verification_requires_configured_backend() -> None:
    with pytest.raises(RegistryError) as exc_info:
        verify_domain_proof(DomainProof("proof.example.com", ASSET_ID), "http")

    assert exc_info.value.error == "verifier_not_configured"


def test_pubkey_bound_domain_proof_parser_accepts_expected_csv_format() -> None:
    proof = parse_pubkey_bound_domain_proof(
        f" context={PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT}, pubkey={PUBKEY.upper()} \n"
    )

    assert proof.context == PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT
    assert proof.pubkey == PUBKEY
    assert expected_pubkey_bound_domain_proof(PUBKEY.upper()) == (
        f"context={PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT},pubkey={PUBKEY}"
    )


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (f"context={PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT}", "domain_verification_failed"),
        (
            f"context={PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT},pubkey={PUBKEY},pubkey={PUBKEY}",
            "domain_verification_failed",
        ),
        (
            f"context={PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT},pubkey={PUBKEY},network=liquid",
            "domain_verification_failed",
        ),
        (f"context=wrong-context,pubkey={PUBKEY}", "domain_verification_failed"),
        (
            f"context={PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT},pubkey=04{'00' * 64}",
            "invalid_pubkey",
        ),
        (
            f"context={PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT},pubkey=02{'00' * 32}",
            "invalid_pubkey",
        ),
    ],
)
def test_pubkey_bound_domain_proof_parser_rejects_invalid_content(content: str, error: str) -> None:
    with pytest.raises(RegistryError) as exc_info:
        parse_pubkey_bound_domain_proof(content)

    assert exc_info.value.error == error


def test_normalized_contract_signature_payload_uses_contract_only_and_excludes_none() -> None:
    contract = ContractMetadata.model_validate(
        {
            "entity": {"domain": "proof.example.com"},
            "name": "Test Asset",
            "precision": 8,
            "ticker": "TEST",
            "version": 2,
            "initial_issuer_pubkey": PUBKEY.upper(),
            "issuer_pubkey": None,
        }
    )

    assert normalized_contract_signature_payload(contract) == (
        b'{"entity":{"domain":"proof.example.com"},"initial_issuer_pubkey":"'
        + PUBKEY.encode()
        + b'","name":"Test Asset","precision":8,"ticker":"TEST","version":2}'
    )


def test_pubkey_bound_domain_signature_verifies_over_normalized_contract() -> None:
    proof = parse_pubkey_bound_domain_proof(expected_pubkey_bound_domain_proof(PUBKEY))
    contract = ContractMetadata.model_validate(
        {
            "entity": {"domain": "proof.example.com"},
            "name": "Test Asset",
            "precision": 8,
            "ticker": "TEST",
            "version": 2,
            "initial_issuer_pubkey": PUBKEY,
        }
    )

    verify_pubkey_bound_domain_signature(
        proof,
        signed_message(normalized_contract_signature_payload(contract)),
        contract,
    )

    with pytest.raises(RegistryError) as exc_info:
        verify_pubkey_bound_domain_signature(
            proof,
            signed_message(b'{"contract":{"name":"Test Asset"}}'),
            contract,
        )

    assert exc_info.value.error == "invalid_signature"


def signed_message(payload: bytes) -> str:
    message_hash = _bitcoin_signed_message_hash(payload.decode("utf-8"))
    signature = wally.ec_sig_from_bytes(PRIVATE_KEY, message_hash, wally.EC_FLAG_ECDSA)
    return base64.b64encode(signature).decode()
