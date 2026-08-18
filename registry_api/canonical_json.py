import hashlib
import json
import math
from typing import Any

from registry_api.errors import ErrorCode, RegistryError


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def action_hash(action: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(action)).hexdigest()


def parse_json_bytes(payload: bytes) -> Any:
    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_non_finite_number,
            parse_float=_parse_finite_float,
        )
        # json.loads accepts escaped unpaired surrogates, but UTF-8 cannot encode them.
        canonical_json_bytes(parsed)
        return parsed
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _invalid_json_error() from exc


def require_canonical_json(payload: bytes) -> Any:
    parsed = parse_json_bytes(payload)
    try:
        canonical = canonical_json_bytes(parsed)
    except (TypeError, ValueError) as exc:
        raise _invalid_json_error() from exc
    if payload != canonical:
        raise RegistryError(
            ErrorCode.NON_CANONICAL_PAYLOAD,
            "request body is not canonical JSON",
            {
                "canonical_payload": canonical.decode("utf-8"),
                "canonical_payload_sha256": hashlib.sha256(canonical).hexdigest(),
            },
        )
    return parsed


def _reject_non_finite_number(value: str) -> Any:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"JSON number is outside the finite float range: {value}")
    return parsed


def _invalid_json_error() -> RegistryError:
    return RegistryError(
        ErrorCode.INVALID_JSON,
        "request body must be valid UTF-8 JSON without non-finite numbers",
        status_code=400,
    )


def contract_hash(contract: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json_bytes(contract)).digest()
    return digest[::-1].hex()
