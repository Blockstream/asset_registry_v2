from importlib.metadata import version
from typing import Any


API_TITLE = "Liquid Asset Registry"
API_SUMMARY = "API for registering, updating, deregistering, querying, and auditing Liquid asset metadata."
API_DESCRIPTION = "FastAPI implementation of the Liquid Asset Registry v2 service with legacy v1 compatibility."
API_VERSION = version("liquid-asset-registry")

OPENAPI_TAGS = [
    {"name": "Health", "description": "Service health endpoint."},
    {"name": "Legacy", "description": "Legacy root-path compatibility endpoints."},
    {
        "name": "Assets",
        "description": "Asset registration, lookup, search, and listing endpoints.",
    },
    {
        "name": "Issuer Actions",
        "description": "Signed issuer mutations, deregistration, migration, and issuer key rotation.",
    },
    {
        "name": "Audit",
        "description": "Append-only asset and global audit log endpoints.",
    },
    {
        "name": "Admin",
        "description": "Signed admin governance, metadata, and moderation endpoints.",
    },
]

ASSET_ID = "aa909f1b" + "0" * 56
PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
ADMIN_PUBKEY = "02abc" + "0" * 61
PREV_ACTION_HASH = "b4b6f2" + "0" * 58
ICON_HASH = "26dee861ebf7b6d298bcb8d7b61bfef865efaca08a371a6ab11dead34efa81ea"

REGISTER_ASSET_EXAMPLES: dict[str, dict[str, Any]] = {
    "v2Contract": {
        "summary": "Register an asset with v2-style contract metadata",
        "value": {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.liquidtestnet.example.net"},
                "initial_issuer_pubkey": PUBKEY,
                "name": "casas",
                "precision": 0,
                "ticker": "casas",
                "version": 2,
            },
            "domain_verification_method": "dns",
        },
    },
    "legacyContract": {
        "summary": "Register an asset with legacy contract metadata",
        "value": {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.liquidtestnet.example.net"},
                "issuer_pubkey": PUBKEY,
                "name": "casas",
                "precision": 0,
                "ticker": "casas",
                "version": 1,
            },
            "domain_verification_method": "dns",
            "initial_issuer_pubkey": PUBKEY,
        },
    },
}

ISSUER_ACTION_EXAMPLES: dict[str, dict[str, Any]] = {
    "replaceCategoryTags": {
        "summary": "Replace category tags",
        "value": {
            "asset_id": ASSET_ID,
            "category_tags": ["stablecoin", "tokenized"],
            "mutable_schema_version": 1,
            "nonce": "9b2b0b3f-8f2e-4f3d-a979-1f2e8a947f87e",
            "operation": "replace_category_tags",
            "prev_action_hash": PREV_ACTION_HASH,
            "signing_context": "liquid-asset-registry-action-v1",
            "timestamp": "2026-04-27T15:30:00.123Z",
        },
    },
    "replaceTradingVenues": {
        "summary": "Replace trading venues using controlled venue identifiers",
        "value": {
            "asset_id": ASSET_ID,
            "mutable_schema_version": 1,
            "nonce": "2099dc1c-e751-4b5e-b570-bb71500a36d0",
            "operation": "replace_trading_venues",
            "prev_action_hash": PREV_ACTION_HASH,
            "signing_context": "liquid-asset-registry-action-v1",
            "timestamp": "2026-04-27T15:30:30.123Z",
            "trading_venues": [
                {"venue": "sideswap", "url": "https://api.sideswap.io/assets/ABT"},
                {"venue": "bitfinex", "url": "https://trading.bitfinex.com/t/ABT:USD"},
            ],
        },
    },
    "replaceCustom": {
        "summary": "Replace the full custom metadata object",
        "value": {
            "asset_id": ASSET_ID,
            "custom": {"issuer_note": "Series A", "isin": "US0000000001"},
            "mutable_schema_version": 1,
            "nonce": "1f4f34e6-81c8-4d8a-b8c4-8e0e9d6b5b28",
            "operation": "replace_custom",
            "prev_action_hash": PREV_ACTION_HASH,
            "signing_context": "liquid-asset-registry-action-v1",
            "timestamp": "2026-04-27T15:30:45.123Z",
        },
    },
    "setCustomField": {
        "summary": "Set one top-level custom metadata field",
        "value": {
            "asset_id": ASSET_ID,
            "custom_key": "isin",
            "mutable_schema_version": 1,
            "nonce": "b0bf357f-bcc1-4d9a-9f54-3421bb95d928",
            "operation": "set_custom_field",
            "prev_action_hash": PREV_ACTION_HASH,
            "signing_context": "liquid-asset-registry-action-v1",
            "timestamp": "2026-04-27T15:30:50.123Z",
            "value": "US0000000002",
        },
    },
    "deleteCustomField": {
        "summary": "Delete one top-level custom metadata field",
        "value": {
            "asset_id": ASSET_ID,
            "custom_key": "isin",
            "mutable_schema_version": 1,
            "nonce": "7df1c4fa-4d57-4f76-80f0-703b81c7c6b5",
            "operation": "delete_custom_field",
            "prev_action_hash": PREV_ACTION_HASH,
            "signing_context": "liquid-asset-registry-action-v1",
            "timestamp": "2026-04-27T15:31:00.123Z",
        },
    },
    "deregister": {
        "summary": "Deregister an asset listing",
        "value": {
            "asset_id": ASSET_ID,
            "nonce": "c0a80134-c355-4dc7-b782-dc7fa39d147d",
            "operation": "deregister",
            "prev_action_hash": PREV_ACTION_HASH,
            "signing_context": "liquid-asset-registry-action-v1",
            "timestamp": "2026-04-27T15:32:00.123Z",
        },
    },
    "rotateIssuerPubkey": {
        "summary": "Rotate issuer public key",
        "value": {
            "asset_id": ASSET_ID,
            "new_issuer_pubkey": ADMIN_PUBKEY,
            "new_issuer_pubkey_signature": "3045022100...",
            "nonce": "414bf082-50a3-44e5-8a6a-bde12124fa7a",
            "operation": "rotate_issuer_pubkey",
            "prev_action_hash": PREV_ACTION_HASH,
            "signing_context": "liquid-asset-registry-action-v1",
            "timestamp": "2026-04-27T15:33:00.123Z",
        },
    },
}

ADMIN_ANNOTATION_EXAMPLES: dict[str, dict[str, Any]] = {
    "markFeatured": {
        "summary": "Mark an asset as featured",
        "value": {
            "actor_pubkey": ADMIN_PUBKEY,
            "asset_id": ASSET_ID,
            "changes": {
                "admin_notes": "manual review complete",
                "asset_type": "stablecoin",
                "featured": True,
            },
            "nonce": "9b2b0b3f-8f2e-4f3d-a979-1f2e8a947f87e",
            "operation": "update_admin_annotations",
            "signing_context": "liquid-asset-registry-admin-action-v1",
            "timestamp": "2026-04-30T15:30:00.123Z",
        },
    }
}

ADMIN_LIFECYCLE_EXAMPLES: dict[str, dict[str, Any]] = {
    "addAdmin": {
        "summary": "Add an admin",
        "value": {
            "actor_pubkey": ADMIN_PUBKEY,
            "admin_pubkey": PUBKEY,
            "friendly_name": "Alice",
            "nonce": "9b2b0b3f-8f2e-4f3d-a979-1f2e8a947f87e",
            "operation": "add_admin",
            "permissions": ["annotate_assets", "review_icons"],
            "signing_context": "liquid-asset-registry-admin-action-v1",
            "timestamp": "2026-04-30T15:30:00.123Z",
        },
    },
    "updateAdminPermissions": {
        "summary": "Update admin permissions",
        "value": {
            "actor_pubkey": ADMIN_PUBKEY,
            "admin_pubkey": PUBKEY,
            "nonce": "0a6a7177-2f77-40f3-8541-5d44f41933f4",
            "operation": "update_admin_permissions",
            "permissions": ["annotate_assets", "manage_admins"],
            "signing_context": "liquid-asset-registry-admin-action-v1",
            "timestamp": "2026-04-30T15:31:00.123Z",
        },
    },
    "updateAdminName": {
        "summary": "Update admin display name",
        "value": {
            "actor_pubkey": ADMIN_PUBKEY,
            "admin_pubkey": PUBKEY,
            "friendly_name": "Alice Ops",
            "nonce": "b4ca4e41-f265-474b-892f-ecb9ff5274c3",
            "operation": "update_admin_name",
            "signing_context": "liquid-asset-registry-admin-action-v1",
            "timestamp": "2026-04-30T15:32:00.123Z",
        },
    },
    "removeAdmin": {
        "summary": "Remove an admin",
        "value": {
            "actor_pubkey": ADMIN_PUBKEY,
            "admin_pubkey": PUBKEY,
            "nonce": "d04e56c4-852d-4d0f-87e7-66a476341bc7",
            "operation": "remove_admin",
            "signing_context": "liquid-asset-registry-admin-action-v1",
            "timestamp": "2026-04-30T15:33:00.123Z",
        },
    },
}

ADMIN_ASSET_EXAMPLES: dict[str, dict[str, Any]] = {
    "forceDelist": {
        "summary": "Force delist an asset",
        "value": {
            "actor_pubkey": ADMIN_PUBKEY,
            "asset_id": ASSET_ID,
            "nonce": "4de0f272-72d8-46ef-96c9-e18f8d340a9a",
            "operation": "force_delist_asset",
            "reason": "policy review",
            "signing_context": "liquid-asset-registry-admin-action-v1",
            "timestamp": "2026-04-30T15:34:00.123Z",
        },
    },
    "forceRelist": {
        "summary": "Force relist an asset",
        "value": {
            "actor_pubkey": ADMIN_PUBKEY,
            "asset_id": ASSET_ID,
            "nonce": "7db05da7-e7a1-4e61-b85f-7b75a675ff95",
            "operation": "force_relist_asset",
            "reason": "review cleared",
            "signing_context": "liquid-asset-registry-admin-action-v1",
            "timestamp": "2026-04-30T15:35:00.123Z",
        },
    },
    "approveIcon": {
        "summary": "Approve a pending icon proposal",
        "value": {
            "actor_pubkey": ADMIN_PUBKEY,
            "asset_id": ASSET_ID,
            "icon_hash": ICON_HASH,
            "nonce": "9e38ad0a-1834-4e58-8403-13bf0282cc4e",
            "operation": "approve_icon",
            "signing_context": "liquid-asset-registry-admin-action-v1",
            "timestamp": "2026-04-30T15:36:00.123Z",
        },
    },
    "rejectIcon": {
        "summary": "Reject a pending icon proposal",
        "value": {
            "actor_pubkey": ADMIN_PUBKEY,
            "asset_id": ASSET_ID,
            "icon_hash": ICON_HASH,
            "nonce": "1a45e737-e661-46a0-a1dd-0e18244f5aaa",
            "operation": "reject_icon",
            "reason": "logo does not fill the circular canvas",
            "signing_context": "liquid-asset-registry-admin-action-v1",
            "timestamp": "2026-04-30T15:37:00.123Z",
        },
    },
}

ICON_PROPOSAL_EXAMPLES: dict[str, dict[str, Any]] = {
    "proposeIcon": {
        "summary": "Propose an issuer-signed PNG icon",
        "value": {
            "action": {
                "asset_id": ASSET_ID,
                "icon_hash": ICON_HASH,
                "nonce": "43dd3379-a2bd-4aab-ad7e-6b0a45334448",
                "operation": "propose_icon",
                "prev_action_hash": PREV_ACTION_HASH,
                "signing_context": "liquid-asset-registry-action-v1",
                "timestamp": "2026-04-30T15:36:00.123Z",
            },
            "icon": "iVBORw0KGgo=",
        },
    }
}

ADMIN_ICON_UPLOAD_EXAMPLES: dict[str, dict[str, Any]] = {
    "setIcon": {
        "summary": "Upload and immediately assign an asset icon",
        "value": {
            "action": {
                "actor_pubkey": ADMIN_PUBKEY,
                "asset_id": ASSET_ID,
                "icon_hash": ICON_HASH,
                "nonce": "78f4f0d2-ff25-42c2-9f23-782efb8f0f4a",
                "operation": "set_icon",
                "signing_context": "liquid-asset-registry-admin-action-v1",
                "timestamp": "2026-04-30T15:37:30.123Z",
            },
            "icon": "iVBORw0KGgo=",
        },
    }
}

ISSUER_ICON_SEARCH_EXAMPLES: dict[str, dict[str, Any]] = {
    "newestFirst": {
        "summary": "List icon proposals made by this issuer key",
        "value": {
            "actor_pubkey": PUBKEY,
            "asset_id": ASSET_ID,
            "operation": "list_icon_proposals",
            "order": "desc",
            "page": 1,
            "page_size": 20,
            "signing_context": "liquid-asset-registry-issuer-query-v1",
            "timestamp": "2026-04-30T15:38:00.123Z",
        },
    }
}

ADMIN_ICON_SEARCH_EXAMPLES: dict[str, dict[str, Any]] = {
    "oldestFirst": {
        "summary": "List the oldest pending icons first",
        "value": {
            "actor_pubkey": ADMIN_PUBKEY,
            "operation": "list_pending_icon_proposals",
            "order": "asc",
            "page": 1,
            "page_size": 20,
            "signing_context": "liquid-asset-registry-admin-query-v1",
            "timestamp": "2026-04-30T15:38:00.123Z",
        },
    }
}
