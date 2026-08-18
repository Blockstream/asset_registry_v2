from collections.abc import Iterable
from datetime import datetime
from json import dumps
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from typing_extensions import TypeAliasType

from registry_api.constants import Actor, IconProposalStatus, Operation
from registry_api.settings import get_settings
from registry_api.validation import (
    ASSET_TYPES,
    DOMAIN_PATTERN,
    DOMAIN_VERIFICATION_METHODS,
    INITIAL_ISSUER_PUBKEY_SOURCES,
    ADMIN_PERMISSIONS,
    normalize_asset_id,
    normalize_domain,
    normalize_pubkey,
    normalize_url,
    reject_nul_characters,
    require_category_tag,
    require_controlled_value,
    require_trading_venue,
    validate_name,
    validate_precision,
    validate_ticker,
)

AssetId = Annotated[
    str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
]
Pubkey = Annotated[
    str, Field(min_length=66, max_length=66, pattern=r"^(02|03)[0-9a-fA-F]{64}$")
]
ActionHash = Annotated[
    str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
]
IconHash = Annotated[
    str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
]
DomainName = Annotated[str, Field(min_length=3, max_length=255, pattern=DOMAIN_PATTERN)]
ContractHash = Annotated[
    str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
]
AssetType = Annotated[
    str,
    Field(
        json_schema_extra={
            "enum": ["AMP_asset", "stablecoin", "security_token", "other"]
        }
    ),
]
CategoryTag = Annotated[
    str,
    Field(
        json_schema_extra={"enum": ["stablecoin", "bond", "fixed-income", "tokenized"]}
    ),
]
TradingVenueName = Annotated[
    str, Field(json_schema_extra={"enum": ["sideswap", "bitfinex"]})
]
AdminPermission = Annotated[
    str,
    Field(
        json_schema_extra={
            "enum": [
                "root",
                "annotate_assets",
                "delist_assets",
                "review_icons",
                "manage_icons",
                "manage_admins",
                "migrate_assets",
            ]
        }
    ),
]
AsciiName = Annotated[
    str,
    Field(
        min_length=1, max_length=255, json_schema_extra={"pattern": r"^[\x01-\x7f]+$"}
    ),
]
V2AssetName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=255,
        json_schema_extra={
            "pattern": r"^[\x01-\x7f]*[\x01-\x08\x0e-\x1b!-\x7f][\x01-\x7f]*$"
        },
    ),
]
Ticker = Annotated[
    str, Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9.\-]+$")
]
LegacyTicker = Annotated[
    str, Field(min_length=3, max_length=24, pattern=r"^[A-Za-z0-9.\-]+$")
]
CustomKey = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[^/\x00]+$")]
NoNulString = Annotated[str, Field(pattern=r"^[^\x00]*$")]
LegacyExtraValue = TypeAliasType(
    "LegacyExtraValue",
    NoNulString
    | int
    | float
    | bool
    | None
    | list["LegacyExtraValue"]
    | dict[NoNulString, "LegacyExtraValue"],
)
MAX_CUSTOM_FIELDS = 32
MAX_CUSTOM_KEY_LENGTH = 64
MAX_CUSTOM_VALUE_BYTES = 2048
MAX_CUSTOM_TOTAL_BYTES = 8192
MAX_LEGACY_EXTRA_FIELDS = 32
MAX_LEGACY_EXTRA_VALUE_BYTES = 2048
MAX_LEGACY_TOTAL_BYTES = 16384


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LegacyAssetRequest(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={"propertyNames": {"pattern": r"^[^\x00]*$"}},
    )

    asset_id: AssetId
    contract: "LegacyContractMetadata" = Field(
        description=(
            "Legacy version 0 contract metadata. The canonical serialized contract object is limited to 4096 bytes; "
            "this default can be changed with ASSET_REGISTRY_LEGACY_CONTRACT_MAX_BYTES. "
            "At most 32 arbitrary extra fields are accepted, and each serialized extra-field value is limited to 2048 bytes."
        )
    )
    domain_verification_method: Literal["dns", "http"] | None = "http"
    __pydantic_extra__: dict[str, LegacyExtraValue] = Field(init=False)

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)

    @model_validator(mode="after")
    def validate_legacy_request_size(self) -> "LegacyAssetRequest":
        _validate_extra_object(self.model_extra or {}, "legacy request")
        _validate_serialized_size(
            self.model_dump(mode="json", exclude_none=True),
            MAX_LEGACY_TOTAL_BYTES,
            "legacy request",
        )
        return self


class LegacyDeletionRequest(StrictModel):
    signature: str = Field(min_length=1)


class LegacyContractValidationRequest(StrictModel):
    contract: "LegacyContractMetadata" = Field(
        description=(
            "Legacy version 0 contract metadata to validate. The canonical serialized contract object is limited to "
            "4096 bytes by default; this can be changed with ASSET_REGISTRY_LEGACY_CONTRACT_MAX_BYTES. At most "
            "32 arbitrary extra fields are accepted, and each serialized extra-field value is limited to 2048 bytes."
        )
    )
    contract_hash: ContractHash

    @field_validator("contract_hash")
    @classmethod
    def validate_contract_hash(cls, value: str) -> str:
        value = value.lower()
        if any(char not in "0123456789abcdef" for char in value):
            raise ValueError("contract_hash must be 64 hex characters")
        return value


class LegacyContractEntity(StrictModel):
    domain: DomainName

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        return normalize_domain(value)


class LegacyContractMetadata(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "description": (
                "Legacy version 0 contract metadata. Arbitrary extra fields are allowed for v1 compatibility, "
                "but the canonical serialized contract object is limited to 4096 bytes by default. "
                "This can be changed with ASSET_REGISTRY_LEGACY_CONTRACT_MAX_BYTES."
            ),
            "propertyNames": {"pattern": r"^[^\x00]*$"},
        },
    )

    version: Literal[0]
    issuer_pubkey: Pubkey
    name: AsciiName
    ticker: LegacyTicker | None = None
    collection: AsciiName | None = None
    precision: int = Field(default=0, ge=0, le=8)
    entity: LegacyContractEntity
    __pydantic_extra__: dict[str, LegacyExtraValue] = Field(init=False)

    @field_validator("version")
    @classmethod
    def validate_legacy_version(cls, value: int) -> int:
        if value != 0:
            raise ValueError("legacy registration only supports contract.version = 0")
        return value

    @field_validator("issuer_pubkey")
    @classmethod
    def validate_issuer_pubkey(cls, value: str) -> str:
        return normalize_pubkey(value)

    @field_validator("name", "collection")
    @classmethod
    def validate_optional_name(cls, value: str | None) -> str | None:
        return validate_name(value) if value is not None else None

    @field_validator("ticker")
    @classmethod
    def validate_legacy_ticker(cls, value: str | None) -> str | None:
        return validate_ticker(value, legacy=True) if value is not None else None

    @field_validator("precision")
    @classmethod
    def validate_legacy_precision(cls, value: int) -> int:
        return validate_precision(value, maximum=8)

    @model_validator(mode="after")
    def validate_legacy_contract_extra_size(self) -> "LegacyContractMetadata":
        _validate_extra_object(self.model_extra or {}, "legacy contract")
        _validate_serialized_size(
            self.model_dump(mode="json", exclude_none=True),
            _legacy_contract_max_bytes(),
            "legacy contract",
        )
        return self


class ContractEntity(StrictModel):
    domain: DomainName

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        return normalize_domain(value)


class ContractMetadata(StrictModel):
    entity: ContractEntity
    name: V2AssetName
    precision: int = Field(ge=0, le=18)
    ticker: Ticker
    version: int = Field(ge=0, json_schema_extra={"minimum": 1})
    initial_issuer_pubkey: Pubkey | None = None
    issuer_pubkey: Pubkey | None = None

    @field_validator("name")
    @classmethod
    def validate_asset_name(cls, value: str) -> str:
        validated = validate_name(value)
        if not validated.strip():
            raise ValueError("name must not be only whitespace")
        return validated

    @field_validator("ticker")
    @classmethod
    def validate_asset_ticker(cls, value: str) -> str:
        return validate_ticker(value)

    @field_validator("precision")
    @classmethod
    def validate_asset_precision(cls, value: int) -> int:
        return validate_precision(value)

    @field_validator("initial_issuer_pubkey", "issuer_pubkey")
    @classmethod
    def validate_optional_pubkey(cls, value: str | None) -> str | None:
        return normalize_pubkey(value) if value is not None else None


class ContractMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    entity: ContractEntity
    name: str = Field(min_length=1, max_length=255)
    precision: int = Field(ge=0, le=18)
    ticker: str | None = Field(default=None, min_length=1, max_length=24)
    version: int = Field(ge=0)
    initial_issuer_pubkey: Pubkey | None = None
    issuer_pubkey: Pubkey | None = None

    @field_validator("name")
    @classmethod
    def validate_asset_name(cls, value: str) -> str:
        return validate_name(value)

    @field_validator("ticker")
    @classmethod
    def validate_asset_ticker(cls, value: str | None) -> str | None:
        return validate_ticker(value) if value is not None else None

    @field_validator("precision")
    @classmethod
    def validate_asset_precision(cls, value: int) -> int:
        return validate_precision(value)

    @field_validator("initial_issuer_pubkey", "issuer_pubkey")
    @classmethod
    def validate_optional_pubkey(cls, value: str | None) -> str | None:
        return normalize_pubkey(value) if value is not None else None


class TradingVenue(StrictModel):
    venue: TradingVenueName
    url: str = Field(
        max_length=2048,
        pattern=r"^[hH][tT][tT][pP][sS]?://[^\s]+$",
        json_schema_extra={"format": "uri"},
    )

    @field_validator("venue")
    @classmethod
    def validate_venue(cls, value: str) -> str:
        return require_trading_venue(value)

    @field_validator("url")
    @classmethod
    def validate_trading_url(cls, value: str) -> str:
        return normalize_url(value)


class MutableMetadata(StrictModel):
    trading_venues: list[TradingVenue] = Field(default_factory=list)
    category_tags: list[CategoryTag] = Field(
        default_factory=list, json_schema_extra={"uniqueItems": True}
    )
    custom: dict[CustomKey, LegacyExtraValue] = Field(default_factory=dict)

    @field_validator("category_tags")
    @classmethod
    def validate_category_tags(cls, value: list[str]) -> list[str]:
        seen = set()
        normalized_tags = []
        for tag in value:
            tag = require_category_tag(tag)
            if tag in seen:
                raise ValueError("category_tags must be unique")
            seen.add(tag)
            normalized_tags.append(tag)
        return normalized_tags

    @field_validator("custom")
    @classmethod
    def validate_custom(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_custom_object(value)
        return value


class RegisterAssetRequest(StrictModel):
    asset_id: AssetId
    contract: ContractMetadata
    domain_verification_method: Literal["dns", "http"] = "http"
    initial_issuer_pubkey: Pubkey | None = None
    mutable: MutableMetadata = Field(default_factory=MutableMetadata)

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)

    @field_validator("domain_verification_method")
    @classmethod
    def validate_domain_verification_method(cls, value: str) -> str:
        return require_controlled_value(
            value, DOMAIN_VERIFICATION_METHODS, "domain verification method"
        )

    @field_validator("initial_issuer_pubkey")
    @classmethod
    def validate_initial_pubkey(cls, value: str | None) -> str | None:
        return normalize_pubkey(value) if value is not None else None

    @model_validator(mode="after")
    def validate_issuer_key_policy(self) -> "RegisterAssetRequest":
        if self.contract.version == 0:
            raise ValueError(
                "v2 registration does not support contract.version = 0; use the legacy registration endpoint"
            )
        if self.contract.version >= 2 and self.contract.initial_issuer_pubkey is None:
            raise PydanticCustomError(
                "issuer_key_policy_conflict",
                "v2+ contract metadata requires contract.initial_issuer_pubkey",
            )
        if (
            self.contract.version < 2
            and self.contract.initial_issuer_pubkey is None
            and self.initial_issuer_pubkey is None
        ):
            raise PydanticCustomError(
                "issuer_key_policy_conflict",
                "legacy contracts require initial_issuer_pubkey when not present in contract",
            )
        return self


class IssuerPubkeyHistoryEntry(StrictModel):
    pubkey: Pubkey
    valid_from_audit_id: int = Field(ge=1)
    valid_until_audit_id: int | None = Field(default=None, ge=1)

    @field_validator("pubkey")
    @classmethod
    def validate_pubkey(cls, value: str) -> str:
        return normalize_pubkey(value)


class AdminActionSummary(StrictModel):
    action: str | None = None
    field: str | None = None
    admin_id: str | None = None
    timestamp: datetime | None = None


class AdminAnnotations(StrictModel):
    asset_type: AssetType | None = None
    featured: bool = False
    malicious: bool = False
    delisted: bool = False
    admin_notes: str | None = Field(
        default=None,
        max_length=4096,
        json_schema_extra={"pattern": r"^[^\x00]*$"},
    )
    last_admin_action: AdminActionSummary | None = None

    @field_validator("asset_type")
    @classmethod
    def validate_asset_type(cls, value: str | None) -> str | None:
        return (
            require_controlled_value(value, ASSET_TYPES, "asset type")
            if value is not None
            else None
        )

    @field_validator("admin_notes")
    @classmethod
    def validate_admin_notes(cls, value: str | None) -> str | None:
        reject_nul_characters(value, "admin_notes")
        return value


class AdminAnnotationsUpdateRequest(StrictModel):
    asset_type: AssetType | None = None
    featured: bool | None = None
    malicious: bool | None = None
    delisted: bool | None = None
    admin_notes: str | None = Field(
        default=None,
        max_length=4096,
        json_schema_extra={"pattern": r"^[^\x00]*$"},
    )

    @field_validator("asset_type")
    @classmethod
    def validate_asset_type(cls, value: str | None) -> str | None:
        return (
            require_controlled_value(value, ASSET_TYPES, "asset type")
            if value is not None
            else None
        )

    @field_validator("admin_notes")
    @classmethod
    def validate_admin_notes(cls, value: str | None) -> str | None:
        reject_nul_characters(value, "admin_notes")
        return value


class AssetIconDescriptor(StrictModel):
    href: str = Field(pattern=r"^/v2/assets/[0-9a-f]{64}/icon/[0-9a-f]{64}\.png$")


class AssetResponse(StrictModel):
    asset_id: AssetId
    contract: ContractMetadataResponse
    initial_issuer_pubkey: Pubkey
    initial_issuer_pubkey_source: str
    current_issuer_pubkey: Pubkey
    issuer_pubkey_history: list[IssuerPubkeyHistoryEntry] = Field(default_factory=list)
    mutable: MutableMetadata
    admin: AdminAnnotations | None = None
    icon: AssetIconDescriptor | None
    status: Literal["active", "deregistered"]
    created_at: datetime
    updated_at: datetime

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)

    @field_validator("initial_issuer_pubkey", "current_issuer_pubkey")
    @classmethod
    def validate_pubkey(cls, value: str) -> str:
        return normalize_pubkey(value)

    @field_validator("initial_issuer_pubkey_source")
    @classmethod
    def validate_pubkey_source(cls, value: str) -> str:
        return require_controlled_value(
            value, INITIAL_ISSUER_PUBKEY_SOURCES, "initial issuer pubkey source"
        )


class AssetListResponse(StrictModel):
    items: list[AssetResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_count: int | None = Field(default=None, ge=0)
    total_pages: int | None = Field(default=None, ge=0)


class BaseIssuerAction(StrictModel):
    signing_context: Literal["liquid-asset-registry-action-v1"]
    asset_id: AssetId
    operation: str
    prev_action_hash: ActionHash = Field(min_length=64, max_length=64)
    timestamp: datetime
    nonce: str = Field(min_length=8, max_length=128)

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)

    @field_validator("prev_action_hash")
    @classmethod
    def validate_prev_action_hash(cls, value: str) -> str:
        value = value.lower()
        if any(char not in "0123456789abcdef" for char in value):
            raise ValueError("prev_action_hash must be 64 hex characters")
        return value


class ReplaceCategoryTagsAction(BaseIssuerAction):
    operation: Literal[Operation.REPLACE_CATEGORY_TAGS]  # pyright: ignore[reportInvalidTypeForm]
    mutable_schema_version: int = Field(ge=1)
    category_tags: list[CategoryTag] = Field(json_schema_extra={"uniqueItems": True})

    @field_validator("category_tags")
    @classmethod
    def validate_category_tags(cls, value: list[str]) -> list[str]:
        return MutableMetadata(
            category_tags=value, trading_venues=[], custom={}
        ).category_tags


class ReplaceTradingVenuesAction(BaseIssuerAction):
    operation: Literal[Operation.REPLACE_TRADING_VENUES]  # pyright: ignore[reportInvalidTypeForm]
    mutable_schema_version: int = Field(ge=1)
    trading_venues: list[TradingVenue]


class ReplaceCustomAction(BaseIssuerAction):
    operation: Literal[Operation.REPLACE_CUSTOM]  # pyright: ignore[reportInvalidTypeForm]
    mutable_schema_version: int = Field(ge=1)
    custom: dict[CustomKey, LegacyExtraValue] = Field(default_factory=dict)

    @field_validator("custom")
    @classmethod
    def validate_custom(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_custom_object(value)
        return value


class SetCustomFieldAction(BaseIssuerAction):
    operation: Literal[Operation.SET_CUSTOM_FIELD]  # pyright: ignore[reportInvalidTypeForm]
    mutable_schema_version: int = Field(ge=1)
    custom_key: CustomKey
    value: LegacyExtraValue

    @field_validator("custom_key")
    @classmethod
    def validate_custom_key(cls, value: str) -> str:
        return _validate_custom_key(value)

    @field_validator("value")
    @classmethod
    def validate_custom_value(cls, value: Any) -> Any:
        _validate_custom_value(value)
        return value


class DeleteCustomFieldAction(BaseIssuerAction):
    operation: Literal[Operation.DELETE_CUSTOM_FIELD]  # pyright: ignore[reportInvalidTypeForm]
    mutable_schema_version: int = Field(ge=1)
    custom_key: CustomKey

    @field_validator("custom_key")
    @classmethod
    def validate_custom_key(cls, value: str) -> str:
        return _validate_custom_key(value)


class DeregisterAction(BaseIssuerAction):
    operation: Literal[Operation.DEREGISTER]  # pyright: ignore[reportInvalidTypeForm]


class RotateIssuerPubkeyAction(BaseIssuerAction):
    operation: Literal[Operation.ROTATE_ISSUER_PUBKEY]  # pyright: ignore[reportInvalidTypeForm]
    new_issuer_pubkey: Pubkey
    new_issuer_pubkey_signature: str | None = None

    @field_validator("new_issuer_pubkey")
    @classmethod
    def validate_new_pubkey(cls, value: str) -> str:
        return normalize_pubkey(value)


class ProposeIconAction(BaseIssuerAction):
    operation: Literal[Operation.PROPOSE_ICON]  # pyright: ignore[reportInvalidTypeForm]
    icon_hash: IconHash

    @field_validator("icon_hash")
    @classmethod
    def validate_icon_hash(cls, value: str) -> str:
        return value.lower()


class IconProposalRequest(StrictModel):
    action: ProposeIconAction
    icon: str = Field(min_length=1, max_length=1_398_104)


IssuerAction = Annotated[
    ReplaceCategoryTagsAction
    | ReplaceTradingVenuesAction
    | ReplaceCustomAction
    | SetCustomFieldAction
    | DeleteCustomFieldAction
    | DeregisterAction
    | RotateIssuerPubkeyAction,
    Field(discriminator="operation"),
]


class BaseAdminAction(StrictModel):
    signing_context: Literal["liquid-asset-registry-admin-action-v1"]
    actor_pubkey: Pubkey
    operation: str
    timestamp: datetime
    nonce: str = Field(min_length=8, max_length=128)

    @field_validator("actor_pubkey")
    @classmethod
    def validate_actor_pubkey(cls, value: str) -> str:
        return normalize_pubkey(value)


class AddAdminAction(BaseAdminAction):
    operation: Literal[Operation.ADD_ADMIN]  # pyright: ignore[reportInvalidTypeForm]
    admin_pubkey: Pubkey
    friendly_name: str = Field(min_length=1, max_length=255)
    permissions: list[AdminPermission] = Field(
        default_factory=list, json_schema_extra={"uniqueItems": True}
    )

    @field_validator("admin_pubkey")
    @classmethod
    def validate_admin_pubkey(cls, value: str) -> str:
        return normalize_pubkey(value)

    @field_validator("friendly_name")
    @classmethod
    def validate_friendly_name(cls, value: str) -> str:
        reject_nul_characters(value, "friendly_name")
        return value

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        return _validate_admin_permissions(value)


class UpdateAdminPermissionsAction(BaseAdminAction):
    operation: Literal[Operation.UPDATE_ADMIN_PERMISSIONS]  # pyright: ignore[reportInvalidTypeForm]
    admin_pubkey: Pubkey
    permissions: list[AdminPermission] = Field(
        default_factory=list, json_schema_extra={"uniqueItems": True}
    )

    @field_validator("admin_pubkey")
    @classmethod
    def validate_admin_pubkey(cls, value: str) -> str:
        return normalize_pubkey(value)

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        return _validate_admin_permissions(value)


class UpdateAdminNameAction(BaseAdminAction):
    operation: Literal[Operation.UPDATE_ADMIN_NAME]  # pyright: ignore[reportInvalidTypeForm]
    admin_pubkey: Pubkey
    friendly_name: str = Field(min_length=1, max_length=255)

    @field_validator("admin_pubkey")
    @classmethod
    def validate_admin_pubkey(cls, value: str) -> str:
        return normalize_pubkey(value)

    @field_validator("friendly_name")
    @classmethod
    def validate_friendly_name(cls, value: str) -> str:
        reject_nul_characters(value, "friendly_name")
        return value


class RemoveAdminAction(BaseAdminAction):
    operation: Literal[Operation.REMOVE_ADMIN]  # pyright: ignore[reportInvalidTypeForm]
    admin_pubkey: Pubkey

    @field_validator("admin_pubkey")
    @classmethod
    def validate_admin_pubkey(cls, value: str) -> str:
        return normalize_pubkey(value)


class UpdateAdminAnnotationsAction(BaseAdminAction):
    operation: Literal[Operation.UPDATE_ADMIN_ANNOTATIONS]  # pyright: ignore[reportInvalidTypeForm]
    asset_id: AssetId
    changes: AdminAnnotationsUpdateRequest

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)


class ForceDelistAssetAction(BaseAdminAction):
    operation: Literal[Operation.FORCE_DELIST_ASSET]  # pyright: ignore[reportInvalidTypeForm]
    asset_id: AssetId
    reason: str | None = Field(
        default=None,
        max_length=4096,
        json_schema_extra={"pattern": r"^[^\x00]*$"},
    )

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        reject_nul_characters(value, "reason")
        return value


class ForceRelistAssetAction(BaseAdminAction):
    operation: Literal[Operation.FORCE_RELIST_ASSET]  # pyright: ignore[reportInvalidTypeForm]
    asset_id: AssetId
    reason: str | None = Field(
        default=None,
        max_length=4096,
        json_schema_extra={"pattern": r"^[^\x00]*$"},
    )

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        reject_nul_characters(value, "reason")
        return value


class ApproveIconAction(BaseAdminAction):
    operation: Literal[Operation.APPROVE_ICON]  # pyright: ignore[reportInvalidTypeForm]
    asset_id: AssetId
    icon_hash: IconHash

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)

    @field_validator("icon_hash")
    @classmethod
    def validate_icon_hash(cls, value: str) -> str:
        return value.lower()


class RejectIconAction(BaseAdminAction):
    operation: Literal[Operation.REJECT_ICON]  # pyright: ignore[reportInvalidTypeForm]
    asset_id: AssetId
    icon_hash: IconHash
    reason: str | None = Field(
        default=None, max_length=4096, json_schema_extra={"pattern": r"^[^\x00]*$"}
    )

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)

    @field_validator("icon_hash")
    @classmethod
    def validate_icon_hash(cls, value: str) -> str:
        return value.lower()

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        reject_nul_characters(value, "reason")
        return value


class SetIconAction(BaseAdminAction):
    operation: Literal[Operation.SET_ICON]  # pyright: ignore[reportInvalidTypeForm]
    asset_id: AssetId
    icon_hash: IconHash

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)

    @field_validator("icon_hash")
    @classmethod
    def validate_icon_hash(cls, value: str) -> str:
        return value.lower()


class AdminIconUploadRequest(StrictModel):
    action: SetIconAction
    icon: str = Field(min_length=1, max_length=1_398_104)


class PendingIconProposalSearchRequest(StrictModel):
    signing_context: Literal["liquid-asset-registry-admin-query-v1"]
    actor_pubkey: Pubkey
    operation: Literal["list_pending_icon_proposals"]
    timestamp: datetime
    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=20, ge=1, le=50)
    order: Literal["asc", "desc"] = "asc"

    @field_validator("actor_pubkey")
    @classmethod
    def validate_actor_pubkey(cls, value: str) -> str:
        return normalize_pubkey(value)


class IssuerIconProposalSearchRequest(StrictModel):
    signing_context: Literal["liquid-asset-registry-issuer-query-v1"]
    actor_pubkey: Pubkey
    asset_id: AssetId
    operation: Literal["list_icon_proposals"]
    timestamp: datetime
    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=20, ge=1, le=50)
    order: Literal["asc", "desc"] = "desc"
    status: IconProposalStatus | None = None

    @field_validator("actor_pubkey")
    @classmethod
    def validate_actor_pubkey(cls, value: str) -> str:
        return normalize_pubkey(value)

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)


class MigrateAssetAction(BaseAdminAction):
    operation: Literal[Operation.MIGRATE_ASSET]  # pyright: ignore[reportInvalidTypeForm]
    asset_id: AssetId

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)


AdminLifecycleAction = Annotated[
    AddAdminAction
    | UpdateAdminPermissionsAction
    | UpdateAdminNameAction
    | RemoveAdminAction,
    Field(discriminator="operation"),
]
AdminAssetAction = Annotated[
    UpdateAdminAnnotationsAction
    | ForceDelistAssetAction
    | ForceRelistAssetAction
    | ApproveIconAction
    | RejectIconAction,
    Field(discriminator="operation"),
]
SignedAdminAssetAction = Annotated[
    UpdateAdminAnnotationsAction
    | ForceDelistAssetAction
    | ForceRelistAssetAction
    | ApproveIconAction
    | RejectIconAction
    | SetIconAction
    | MigrateAssetAction,
    Field(discriminator="operation"),
]


class AuditEntry(StrictModel):
    audit_id: int = Field(ge=1)
    server_received_at: datetime
    actor: Literal[Actor.ISSUER, Actor.ADMIN, Actor.SYSTEM]  # pyright: ignore[reportInvalidTypeForm]
    verified_pubkey: Pubkey | None = None
    admin_id: str | None = None
    action: dict[str, Any]
    action_hash: ActionHash | None = None
    signature: str | None = None

    @field_validator("verified_pubkey")
    @classmethod
    def validate_verified_pubkey(cls, value: str | None) -> str | None:
        return normalize_pubkey(value) if value is not None else None

    @field_validator("action_hash")
    @classmethod
    def validate_action_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.lower()
        if any(char not in "0123456789abcdef" for char in value):
            raise ValueError("action_hash must be 64 hex characters")
        return value


class AuditLogResponse(StrictModel):
    items: list[AuditEntry]
    next_since_audit_id: int | None = None


class LatestActionHashResponse(StrictModel):
    asset_id: AssetId
    action_hash: ActionHash
    audit_id: int = Field(ge=1)
    operation: str
    server_received_at: datetime

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return normalize_asset_id(value)

    @field_validator("action_hash")
    @classmethod
    def validate_action_hash(cls, value: str) -> str:
        value = value.lower()
        if any(char not in "0123456789abcdef" for char in value):
            raise ValueError("action_hash must be 64 hex characters")
        return value


class IssuerActionResponse(StrictModel):
    status: Literal["applied", "idempotent_retry"]
    audit_entry: AuditEntry
    asset: AssetResponse | None = None


class AdminActionResponse(StrictModel):
    status: Literal["applied", "idempotent_retry"]
    audit_entry: AuditEntry


class IconProposalSummary(StrictModel):
    proposal_id: str
    asset_id: AssetId
    icon_hash: IconHash
    status: IconProposalStatus
    proposed_at: datetime
    decided_at: datetime | None = None
    obsoleted_at: datetime | None = None


class IconProposalResponse(StrictModel):
    status: Literal["applied", "idempotent_retry"]
    proposal: IconProposalSummary
    audit_entry: AuditEntry


class PendingIconProposal(StrictModel):
    proposal_id: str
    asset_id: AssetId
    icon_hash: IconHash
    icon: str
    proposed_at: datetime


class PendingIconProposalListResponse(StrictModel):
    items: list[PendingIconProposal]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=50)
    total_count: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class IssuerIconProposal(IconProposalSummary):
    icon: str | None = Field(default=None, exclude_if=lambda value: value is None)


class IssuerIconProposalListResponse(StrictModel):
    items: list[IssuerIconProposal]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=50)
    total_count: int = Field(ge=0)
    total_pages: int = Field(ge=0)


def _validate_admin_permissions(value: list[str]) -> list[str]:
    seen = set()
    for permission in value:
        require_controlled_value(permission, ADMIN_PERMISSIONS, "admin permission")
        if permission in seen:
            raise ValueError("permissions must be unique")
        seen.add(permission)
    return value


def _validate_custom_object(value: dict[str, Any]) -> None:
    if len(value) > MAX_CUSTOM_FIELDS:
        raise ValueError(
            f"custom metadata must not contain more than {MAX_CUSTOM_FIELDS} fields"
        )
    _validate_custom_keys(value.keys())
    for field_value in value.values():
        _validate_custom_value(field_value)
    _validate_custom_total_size(value)


def _validate_extra_object(value: dict[str, Any], label: str) -> None:
    if len(value) > MAX_LEGACY_EXTRA_FIELDS:
        raise ValueError(
            f"{label} must not contain more than {MAX_LEGACY_EXTRA_FIELDS} extra fields"
        )
    reject_nul_characters(value, label)
    for field_value in value.values():
        _validate_serialized_size(
            field_value, MAX_LEGACY_EXTRA_VALUE_BYTES, f"{label} extra field"
        )


def _validate_serialized_size(value: Any, maximum: int, label: str) -> None:
    serialized = _canonical_json_bytes(value)
    if len(serialized) > maximum:
        raise ValueError(f"{label} must be no larger than {maximum} bytes")


def _legacy_contract_max_bytes() -> int:
    return get_settings().legacy_contract_max_bytes


def _validate_custom_keys(keys: Iterable[str]) -> None:
    seen = set()
    for key in keys:
        normalized = _validate_custom_key(key)
        if normalized in seen:
            raise ValueError("custom metadata keys must be unique")
        seen.add(normalized)


def _validate_custom_key(value: str) -> str:
    reject_nul_characters(value, "custom metadata key")
    if not value or "/" in value:
        raise ValueError("custom metadata key must identify one top-level key")
    if len(value) > MAX_CUSTOM_KEY_LENGTH:
        raise ValueError(
            f"custom metadata key must be no longer than {MAX_CUSTOM_KEY_LENGTH} characters"
        )
    return value


def _validate_custom_value(value: Any) -> None:
    reject_nul_characters(value, "custom metadata value")
    _validate_custom_value_size(value)


def _validate_custom_value_size(value: Any) -> None:
    serialized = _canonical_json_bytes(value)
    if len(serialized) > MAX_CUSTOM_VALUE_BYTES:
        raise ValueError(
            f"custom metadata value must be no larger than {MAX_CUSTOM_VALUE_BYTES} bytes"
        )


def _validate_custom_total_size(value: dict[str, Any]) -> None:
    serialized = _canonical_json_bytes(value)
    if len(serialized) > MAX_CUSTOM_TOTAL_BYTES:
        raise ValueError(
            f"custom metadata must be no larger than {MAX_CUSTOM_TOTAL_BYTES} bytes"
        )


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("custom metadata values must be JSON serializable") from exc
