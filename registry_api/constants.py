from enum import StrEnum


class Actor(StrEnum):
    ISSUER = "issuer"
    ADMIN = "admin"
    SYSTEM = "system"


class IconProposalStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    APPROVED = "approved"


class Operation(StrEnum):
    REGISTER = "register"
    LEGACY_REGISTER = "legacy_register"
    LEGACY_DEREGISTER = "legacy_deregister"
    MIGRATE_CONTRACT_METADATA = "migrate_contract_metadata"

    REPLACE_CATEGORY_TAGS = "replace_category_tags"
    REPLACE_TRADING_VENUES = "replace_trading_venues"
    REPLACE_CUSTOM = "replace_custom"
    SET_CUSTOM_FIELD = "set_custom_field"
    DELETE_CUSTOM_FIELD = "delete_custom_field"
    DEREGISTER = "deregister"
    ROTATE_ISSUER_PUBKEY = "rotate_issuer_pubkey"
    PROPOSE_ICON = "propose_icon"

    ADD_ADMIN = "add_admin"
    UPDATE_ADMIN_PERMISSIONS = "update_admin_permissions"
    UPDATE_ADMIN_NAME = "update_admin_name"
    REMOVE_ADMIN = "remove_admin"
    UPDATE_ADMIN_ANNOTATIONS = "update_admin_annotations"
    FORCE_DELIST_ASSET = "force_delist_asset"
    FORCE_RELIST_ASSET = "force_relist_asset"
    APPROVE_ICON = "approve_icon"
    REJECT_ICON = "reject_icon"
    SET_ICON = "set_icon"
    MIGRATE_ASSET = "migrate_asset"
    IMPORT_LEGACY_ICON = "import_legacy_icon"


ADMIN_LIFECYCLE_OPERATIONS = frozenset(
    {
        Operation.ADD_ADMIN,
        Operation.UPDATE_ADMIN_PERMISSIONS,
        Operation.UPDATE_ADMIN_NAME,
        Operation.REMOVE_ADMIN,
    }
)
