import logging
from dataclasses import dataclass
from typing import Any

import httpx

from registry_api.schemas import LegacyAssetRequest, LegacyDeletionRequest
from registry_api.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShadowOutcome:
    classification: str
    legacy_status_code: int | None = None
    legacy_response: Any = None
    error: str | None = None


def ensure_legacy_registration_written(settings: Settings, request: LegacyAssetRequest) -> ShadowOutcome:
    if not settings.legacy_shadow_write:
        return ShadowOutcome("shadow_disabled")

    if not settings.legacy_base_url:
        outcome = ShadowOutcome("legacy_unreachable", error="ASSET_REGISTRY_LEGACY_BASE_URL is not configured")
        _log_shadow_outcome(outcome, request.model_dump(exclude_none=True), None)
        return outcome

    request_body = request.model_dump(exclude_none=True)
    base_url = str(settings.legacy_base_url).rstrip("/")
    try:
        with httpx.Client(timeout=settings.legacy_timeout_seconds) as client:
            response = client.post(base_url + "/", json=request_body)
            legacy_payload = _response_payload(response)
            if 200 <= response.status_code < 300:
                outcome = ShadowOutcome("legacy_write_succeeded", response.status_code, legacy_payload)
            else:
                outcome = ShadowOutcome("legacy_write_failed", response.status_code, legacy_payload)
            _log_shadow_outcome(outcome, request_body, None)
            return outcome
    except httpx.HTTPError as exc:
        outcome = ShadowOutcome("legacy_unreachable", error=str(exc))
        _log_shadow_outcome(outcome, request_body, None)
        return outcome


def ensure_legacy_deregistration_written(
    settings: Settings,
    asset_id: str,
    request: LegacyDeletionRequest,
) -> ShadowOutcome:
    if not settings.legacy_shadow_write:
        return ShadowOutcome("shadow_disabled")

    request_body = request.model_dump(exclude_none=True)
    request_body["asset_id"] = asset_id
    if not settings.legacy_base_url:
        outcome = ShadowOutcome("legacy_unreachable", error="ASSET_REGISTRY_LEGACY_BASE_URL is not configured")
        _log_shadow_outcome(outcome, request_body, None)
        return outcome

    base_url = str(settings.legacy_base_url).rstrip("/")
    try:
        with httpx.Client(timeout=settings.legacy_timeout_seconds) as client:
            response = client.request("DELETE", f"{base_url}/{asset_id}", json=request.model_dump(exclude_none=True))
            legacy_payload = _response_payload(response)
            if 200 <= response.status_code < 300:
                outcome = ShadowOutcome("legacy_delete_succeeded", response.status_code, legacy_payload)
            else:
                outcome = ShadowOutcome("legacy_delete_failed", response.status_code, legacy_payload)
            _log_shadow_outcome(outcome, request_body, None)
            return outcome
    except httpx.HTTPError as exc:
        outcome = ShadowOutcome("legacy_unreachable", error=str(exc))
        _log_shadow_outcome(outcome, request_body, None)
        return outcome


def _compare_registration_responses(legacy_response: Any, new_response: dict[str, Any]) -> str:
    if not isinstance(legacy_response, dict):
        return "semantic_match_response_differs"
    semantic_fields = ("asset_id", "version", "issuer_pubkey", "name", "ticker", "precision", "entity")
    for field in semantic_fields:
        if legacy_response.get(field) != new_response.get(field):
            return "semantic_match_response_differs"
    return "matched" if legacy_response == new_response else "semantic_match_response_differs"


def _response_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _log_shadow_outcome(outcome: ShadowOutcome, request_body: dict[str, Any], new_response: dict[str, Any] | None) -> None:
    logger.info(
        "legacy shadow write outcome",
        extra={
            "classification": outcome.classification,
            "asset_id": request_body.get("asset_id"),
            "legacy_status_code": outcome.legacy_status_code,
            "legacy_response": outcome.legacy_response,
            "new_response": new_response,
            "error": outcome.error,
        },
    )
