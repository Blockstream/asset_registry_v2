from enum import StrEnum
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, ValidationError


class ErrorCode(StrEnum):
    ADMIN_CONFLICT = "admin_conflict"
    ADMIN_NOT_FOUND = "admin_not_found"
    ASSET_CONFLICT = "asset_conflict"
    ASSET_ID_MISMATCH = "asset_id_mismatch"
    ASSET_NOT_FOUND = "asset_not_found"
    CHAIN_VERIFICATION_FAILED = "chain_verification_failed"
    CHAIN_VERIFIER_ERROR = "chain_verifier_error"
    CHAIN_VERIFIER_NOT_CONFIGURED = "chain_verifier_not_configured"
    CHAIN_VERIFIER_UNREACHABLE = "chain_verifier_unreachable"
    DOMAIN_VERIFICATION_FAILED = "domain_verification_failed"
    DOMAIN_VERIFIER_UNREACHABLE = "domain_verifier_unreachable"
    FORBIDDEN = "forbidden"
    INVALID_JSON = "invalid_json"
    INVALID_PUBKEY = "invalid_pubkey"
    INVALID_SIGNATURE = "invalid_signature"
    INVALID_ICON = "invalid_icon"
    ICON_HASH_MISMATCH = "icon_hash_mismatch"
    ICON_NOT_FOUND = "icon_not_found"
    ICON_PENDING_CONFLICT = "icon_pending_conflict"
    ICON_PROPOSAL_ALREADY_DECIDED = "icon_proposal_already_decided"
    ICON_PROPOSAL_NOT_FOUND = "icon_proposal_not_found"
    LAST_ROOT_ADMIN = "last_root_admin"
    LEGACY_REGISTRY_WRITE_FAILED = "legacy_registry_write_failed"
    MUTABLE_SCHEMA_VERSION_MISMATCH = "mutable_schema_version_mismatch"
    NON_CANONICAL_PAYLOAD = "non_canonical_payload"
    NONCE_CONFLICT = "nonce_conflict"
    NO_OP_ACTION = "no_op_action"
    PREV_ACTION_HASH_MISMATCH = "prev_action_hash_mismatch"
    RATE_LIMITED = "rate_limited"
    STALE_TIMESTAMP = "stale_timestamp"
    UNSUPPORTED_DOMAIN_VERIFICATION_METHOD = "unsupported_domain_verification_method"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    VALIDATION_ERROR = "validation_error"
    VERIFIER_NOT_CONFIGURED = "verifier_not_configured"


class RegistryError(Exception):
    def __init__(
        self,
        error: ErrorCode | str,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.error = str(error)
        self.message = message
        self.details = details
        self.status_code = status_code


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str
    message: str | None = None
    details: dict[str, Any] | None = None


def clean_pydantic_errors(exc: ValidationError) -> list[dict[str, Any]]:
    cleaned = []
    for error in exc.errors():
        error = dict(error)
        error.pop("input", None)
        if "ctx" in error:
            error["ctx"] = jsonable_encoder(error["ctx"])
        cleaned.append(error)
    return cleaned
