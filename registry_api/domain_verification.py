from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from typing import Literal, Protocol

import wallycore as wally

from registry_api.canonical_json import canonical_json_bytes
from registry_api.errors import ErrorCode, RegistryError
from registry_api.schemas import ContractMetadata
from registry_api.signatures import verify_canonical_payload_signature
from registry_api.validation import normalize_domain, normalize_pubkey

PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT = "liquid-asset-registry-v2"
PUBKEY_BOUND_DOMAIN_PROOF_KEYS = frozenset({"context", "pubkey"})


class HttpTextFetcher(Protocol):
    def __call__(self, url: str) -> str:
        ...


class TxtResolver(Protocol):
    def __call__(self, domain: str) -> Sequence[str]:
        ...


@dataclass(frozen=True)
class DomainProof:
    domain: str
    asset_id: str
    ticker: str | None = None


@dataclass(frozen=True)
class PubkeyBoundDomainProof:
    context: str
    pubkey: str


def expected_http_proof(domain: str, asset_id: str) -> str:
    return f"Authorize linking the domain name {domain} to the Liquid asset {asset_id}"


def expected_dns_proof(asset_id: str, ticker: str | None = None) -> str:
    return f"liquid-asset-verification={asset_id},{ticker or ''}"


def expected_pubkey_bound_domain_proof(pubkey: str) -> str:
    return f"context={PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT},pubkey={normalize_pubkey(pubkey)}"


def parse_pubkey_bound_domain_proof(content: str) -> PubkeyBoundDomainProof:
    fields: dict[str, str] = {}

    for raw_pair in content.strip().split(","):
        pair = raw_pair.strip()
        if not pair:
            raise RegistryError(
                ErrorCode.DOMAIN_VERIFICATION_FAILED,
                "pubkey-bound domain proof contains an empty field",
            )

        key, separator, value = pair.partition("=")
        if separator != "=":
            raise RegistryError(
                ErrorCode.DOMAIN_VERIFICATION_FAILED,
                "pubkey-bound domain proof fields must use key=value format",
            )

        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise RegistryError(
                ErrorCode.DOMAIN_VERIFICATION_FAILED,
                "pubkey-bound domain proof fields must include non-empty keys and values",
            )
        if key in fields:
            raise RegistryError(
                ErrorCode.DOMAIN_VERIFICATION_FAILED,
                f"pubkey-bound domain proof contains duplicate field: {key}",
                {"field": key},
            )
        fields[key] = value

    unknown_keys = sorted(set(fields) - PUBKEY_BOUND_DOMAIN_PROOF_KEYS)
    if unknown_keys:
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFICATION_FAILED,
            "pubkey-bound domain proof contains unsupported fields",
            {"fields": unknown_keys},
        )

    missing_keys = sorted(PUBKEY_BOUND_DOMAIN_PROOF_KEYS - set(fields))
    if missing_keys:
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFICATION_FAILED,
            "pubkey-bound domain proof is missing required fields",
            {"fields": missing_keys},
        )

    context = fields["context"]
    if context != PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT:
        raise RegistryError(
            ErrorCode.DOMAIN_VERIFICATION_FAILED,
            "pubkey-bound domain proof context is not supported",
            {"expected_context": PUBKEY_BOUND_DOMAIN_PROOF_CONTEXT},
        )

    try:
        pubkey = normalize_pubkey(fields["pubkey"])
        wally.ec_public_key_verify(bytes.fromhex(pubkey))
    except ValueError as exc:
        raise RegistryError(ErrorCode.INVALID_PUBKEY, "pubkey-bound domain proof pubkey is invalid") from exc

    return PubkeyBoundDomainProof(context=context, pubkey=pubkey)


def normalized_contract_signature_payload(contract: ContractMetadata | Mapping[str, Any]) -> bytes:
    if isinstance(contract, ContractMetadata):
        normalized_contract = contract.model_dump(mode="json", exclude_none=True)
    else:
        normalized_contract = dict(contract)
    return canonical_json_bytes(normalized_contract)


def verify_pubkey_bound_domain_signature(
    proof: PubkeyBoundDomainProof,
    signature: str,
    contract: ContractMetadata | Mapping[str, Any],
    *,
    registration_payload: bytes | None = None,
) -> None:
    payload = normalized_contract_signature_payload(contract)
    try:
        verify_canonical_payload_signature(proof.pubkey, signature, payload)
    except RegistryError as exc:
        if exc.error == ErrorCode.INVALID_SIGNATURE:
            if registration_payload is not None:
                try:
                    verify_canonical_payload_signature(proof.pubkey, signature, registration_payload)
                except RegistryError:
                    pass
                else:
                    raise RegistryError(
                        ErrorCode.INVALID_SIGNATURE,
                        "domain proof signature must cover the normalized contract JSON, not the registration request body",
                        {"expected_payload": "normalized_contract"},
                        status_code=401,
                    ) from exc
            raise RegistryError(
                ErrorCode.INVALID_SIGNATURE,
                "pubkey-bound domain proof signature verification failed",
                status_code=401,
            ) from exc
        raise


def proof_url(domain: str, asset_id: str) -> str:
    scheme = "http" if domain.endswith(".onion") else "https"
    return f"{scheme}://{domain}/.well-known/liquid-asset-proof-{asset_id}"


def verify_http_domain_proof(proof: DomainProof, fetch_text: HttpTextFetcher) -> None:
    domain = normalize_domain(proof.domain)
    body = fetch_text(proof_url(domain, proof.asset_id))
    if body.rstrip() != expected_http_proof(domain, proof.asset_id):
        raise RegistryError(ErrorCode.DOMAIN_VERIFICATION_FAILED, "HTTP domain proof contents did not match")


def verify_dns_domain_proof(proof: DomainProof, resolve_txt: TxtResolver) -> None:
    domain = normalize_domain(proof.domain)
    expected = expected_dns_proof(proof.asset_id, proof.ticker)
    if expected not in resolve_txt(domain):
        raise RegistryError(ErrorCode.DOMAIN_VERIFICATION_FAILED, "DNS TXT domain proof was not found")


def verify_domain_proof(
    proof: DomainProof,
    method: Literal["http", "dns"],
    *,
    fetch_text: HttpTextFetcher | None = None,
    resolve_txt: TxtResolver | None = None,
) -> None:
    if method == "http":
        if fetch_text is None:
            raise RegistryError(ErrorCode.VERIFIER_NOT_CONFIGURED, "HTTP domain verifier is not configured")
        verify_http_domain_proof(proof, fetch_text)
        return
    if method == "dns":
        if resolve_txt is None:
            raise RegistryError(ErrorCode.VERIFIER_NOT_CONFIGURED, "DNS domain verifier is not configured")
        verify_dns_domain_proof(proof, resolve_txt)
        return
    raise RegistryError(ErrorCode.UNSUPPORTED_DOMAIN_VERIFICATION_METHOD, "unsupported domain verification method")
