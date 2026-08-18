import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import NoReturn, Protocol

import httpx
import wallycore as wally

from registry_api.errors import ErrorCode, RegistryError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IssuanceCommitment:
    asset_id: str
    contract_hash: str
    issuance_txid: str | None = None
    issuance_vin: int | None = None


class ChainVerifier(Protocol):
    def verify_issuance_commitment(self, commitment: IssuanceCommitment) -> None:
        ...


class UnconfiguredChainVerifier:
    def verify_issuance_commitment(self, commitment: IssuanceCommitment) -> None:
        raise RegistryError(
            ErrorCode.CHAIN_VERIFIER_NOT_CONFIGURED,
            "chain issuance commitment verification is not configured",
            {"asset_id": commitment.asset_id},
        )


class TrustingChainVerifier:
    """Development-only verifier for tests and local wiring before chain integration."""

    def verify_issuance_commitment(self, commitment: IssuanceCommitment) -> None:
        return None


def _display_hash_to_internal(hash_hex: str) -> bytes:
    return bytes.fromhex(hash_hex)[::-1]


def _internal_hash_to_display(hash_bytes: bytes) -> str:
    return hash_bytes[::-1].hex()


def generate_asset_entropy(prevout_txid: str, prevout_vout: int, contract_hash: str) -> bytes:
    return wally.tx_elements_issuance_generate_entropy(
        _display_hash_to_internal(prevout_txid),
        prevout_vout,
        _display_hash_to_internal(contract_hash),
    )


def asset_id_from_entropy(entropy: bytes) -> str:
    return _internal_hash_to_display(wally.tx_elements_issuance_calculate_asset(entropy))


def derive_asset_id(prevout_txid: str, prevout_vout: int, contract_hash: str) -> str:
    return asset_id_from_entropy(generate_asset_entropy(prevout_txid, prevout_vout, contract_hash))


@dataclass(frozen=True)
class ParsedIssuanceInput:
    previous_txid: str
    previous_vout: int
    asset_entropy: str


def parse_issuance_input(tx_hex: str, vin: int) -> ParsedIssuanceInput:
    if vin < 0:
        raise RegistryError(ErrorCode.CHAIN_VERIFICATION_FAILED, "issuance transaction missing input")

    try:
        tx = wally.tx_from_hex(tx_hex.strip(), wally.WALLY_TX_FLAG_USE_ELEMENTS)
    except (TypeError, ValueError) as exc:
        raise RegistryError(ErrorCode.CHAIN_VERIFICATION_FAILED, "invalid issuance transaction") from exc

    if vin >= wally.tx_get_num_inputs(tx):
        raise RegistryError(ErrorCode.CHAIN_VERIFICATION_FAILED, "issuance transaction missing input")

    has_issuance = (
        wally.tx_get_input_issuance_amount_len(tx, vin) > 0
        or wally.tx_get_input_inflation_keys_len(tx, vin) > 0
    )
    if not has_issuance:
        raise RegistryError(ErrorCode.CHAIN_VERIFICATION_FAILED, "input has no issuance")

    asset_entropy = wally.tx_get_input_entropy(tx, vin)
    return ParsedIssuanceInput(
        previous_txid=_internal_hash_to_display(wally.tx_get_input_txhash(tx, vin)),
        previous_vout=wally.tx_get_input_index(tx, vin),
        asset_entropy=_internal_hash_to_display(asset_entropy),
    )


class EsploraChainVerifier:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        get_json: Callable[[str], dict] | None = None,
        get_text: Callable[[str], str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._get_json = get_json
        self._get_text = get_text

    def verify_issuance_commitment(self, commitment: IssuanceCommitment) -> None:
        asset_data = self._json(f"/asset/{commitment.asset_id}")
        issuance_txin = asset_data.get("issuance_txin") or {}
        issuance_prevout = asset_data.get("issuance_prevout") or {}
        txid = issuance_txin.get("txid")
        vin = issuance_txin.get("vin")
        prevout_txid = issuance_prevout.get("txid")
        prevout_vout = issuance_prevout.get("vout")
        if not isinstance(txid, str) or not isinstance(vin, int) or not isinstance(prevout_txid, str) or not isinstance(prevout_vout, int):
            raise RegistryError(ErrorCode.CHAIN_VERIFICATION_FAILED, "Esplora asset response is missing issuance metadata")

        tx_hex = self._text(f"/tx/{txid}/hex")
        status = self._json(f"/tx/{txid}/status")
        if not status.get("confirmed"):
            raise RegistryError(ErrorCode.CHAIN_VERIFICATION_FAILED, "issuance transaction is unconfirmed")

        issuance_input = parse_issuance_input(tx_hex, vin)
        if (issuance_input.previous_txid, issuance_input.previous_vout) != (prevout_txid, prevout_vout):
            raise RegistryError(ErrorCode.CHAIN_VERIFICATION_FAILED, "issuance prevout mismatch")
        if issuance_input.asset_entropy != commitment.contract_hash:
            raise RegistryError(ErrorCode.CHAIN_VERIFICATION_FAILED, "issuance entropy does not match contract hash")

        derived_asset_id = derive_asset_id(prevout_txid, prevout_vout, commitment.contract_hash)
        if derived_asset_id != commitment.asset_id:
            raise RegistryError(
                ErrorCode.CHAIN_VERIFICATION_FAILED,
                "asset id does not match issuance commitment",
                {"expected_asset_id": derived_asset_id, "actual_asset_id": commitment.asset_id},
            )

    def _json(self, path: str) -> dict:
        if self._get_json is not None:
            return self._get_json(path)
        response = self._get(path)
        try:
            return response.json()
        except ValueError as exc:
            self._raise_esplora_service_error(path, response.status_code, response.text, "Esplora returned invalid JSON", exc)

    def _text(self, path: str) -> str:
        if self._get_text is not None:
            return self._get_text(path)
        response = self._get(path)
        return response.text

    def _get(self, path: str) -> httpx.Response:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.base_url}{path}")
        except httpx.RequestError as exc:
            raise RegistryError(
                ErrorCode.CHAIN_VERIFIER_UNREACHABLE,
                "failed to connect to Esplora API",
                {"path": path},
                status_code=503,
            ) from exc

        if response.status_code == 404:
            resource = _esplora_resource_name(path)
            raise RegistryError(
                ErrorCode.CHAIN_VERIFICATION_FAILED,
                f"{resource} not found",
                {"path": path},
                status_code=404,
            )
        if response.status_code >= 400:
            detail = response.text[:500]
            self._raise_esplora_service_error(
                path,
                response.status_code,
                detail,
                "Esplora returned an error response",
                None,
            )
        return response

    def _raise_esplora_service_error(
        self,
        path: str,
        status_code: int,
        response_body: str,
        reason: str,
        exc: Exception | None,
    ) -> NoReturn:
        logger.warning(
            "Esplora chain verifier request failed",
            extra={
                "path": path,
                "status_code": status_code,
                "response_body": response_body,
                "reason": reason,
            },
            exc_info=exc,
        )
        raise RegistryError(
            ErrorCode.CHAIN_VERIFIER_ERROR,
            "the registry is having trouble querying Esplora; this is not a problem with the submitted asset",
            {"path": path},
            status_code=503,
        ) from exc


def _esplora_resource_name(path: str) -> str:
    if path.startswith("/asset/"):
        return "asset"
    if path.endswith("/hex"):
        return "issuance transaction"
    if path.endswith("/status"):
        return "issuance transaction status"
    if path.startswith("/tx/"):
        return "transaction"
    return "requested chain resource"
