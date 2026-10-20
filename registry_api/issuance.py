"""Stored issuance references, separate from the hash-committed contract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from registry_api.models import Asset


class IssuanceReference(BaseModel):
    # External Esplora/listing objects may gain fields independently of this API.
    model_config = ConfigDict(extra="ignore")

    txid: Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]

    @field_validator("txid")
    @classmethod
    def normalize_txid(cls, value: str) -> str:
        return value.lower()


class IssuanceTxin(IssuanceReference):
    vin: Annotated[int, Field(strict=True, ge=0, le=4294967295)]


class IssuancePrevout(IssuanceReference):
    vout: Annotated[int, Field(strict=True, ge=0, le=4294967295)]


class IssuanceMetadata(BaseModel):
    issuance_txin: IssuanceTxin | None = None
    issuance_prevout: IssuancePrevout | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> IssuanceMetadata:
        return cls.model_validate({key: payload.get(key) for key in cls.model_fields})

    def asset_columns(self) -> dict[str, str | int | None]:
        return {
            "issuance_txid": self.issuance_txin.txid if self.issuance_txin else None,
            "issuance_vin": self.issuance_txin.vin if self.issuance_txin else None,
            "issuance_prevout_txid": self.issuance_prevout.txid
            if self.issuance_prevout
            else None,
            "issuance_prevout_vout": self.issuance_prevout.vout
            if self.issuance_prevout
            else None,
        }


def issuance_payload_from_asset(
    asset: Asset, *, exclude_none: bool = False
) -> dict[str, Any]:
    """Project database-constrained columns without constructing input models."""
    payload = {
        "issuance_txin": {"txid": asset.issuance_txid, "vin": asset.issuance_vin}
        if asset.issuance_txid is not None and asset.issuance_vin is not None
        else None,
        "issuance_prevout": {
            "txid": asset.issuance_prevout_txid,
            "vout": asset.issuance_prevout_vout,
        }
        if asset.issuance_prevout_txid is not None
        and asset.issuance_prevout_vout is not None
        else None,
    }
    return {
        key: value
        for key, value in payload.items()
        if value is not None or not exclude_none
    }
