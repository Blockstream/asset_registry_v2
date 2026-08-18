import base64
import os
from datetime import UTC, datetime, timedelta

import pytest
import wallycore as wally
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from registry_api.admin import submit_admin_asset_action, update_admin_annotations
from registry_api.admin_actions import bootstrap_genesis_admin, submit_admin_lifecycle_action
from registry_api.audit import get_asset_audit_log, search_audit_log
from registry_api.canonical_json import canonical_json
from registry_api.errors import RegistryError
from registry_api.schemas import RegisterAssetRequest
from registry_api.signatures import _bitcoin_signed_message_hash
from registry_api.v2_assets import register_v2_asset


ASSET_ID = "ab909f1b00000000000000000000000000000000000000000000000000000000"
ASSET_ID_2 = "ac909f1b00000000000000000000000000000000000000000000000000000000"
PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
PUBKEY_2 = "0282375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
NOW = datetime(2026, 4, 30, 16, 0, tzinfo=UTC)
ROOT_PRIVATE_KEY = 1
ADMIN_PRIVATE_KEY = 2
ROOT_PUBKEY = ""
ADMIN_PUBKEY = ""


@pytest.fixture(scope="module", autouse=True)
def compute_admin_pubkeys() -> None:
    global ROOT_PUBKEY, ADMIN_PUBKEY
    ROOT_PUBKEY = pubkey_for_private_key(ROOT_PRIVATE_KEY)
    ADMIN_PUBKEY = pubkey_for_private_key(ADMIN_PRIVATE_KEY)


@pytest.fixture()
def session_factory():
    if not os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL"):
        pytest.skip("ASSET_REGISTRY_TEST_DATABASE_URL is required for admin and audit database tests")
    engine = create_engine(os.environ["ASSET_REGISTRY_TEST_DATABASE_URL"])
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    cleanup_database(engine)
    try:
        yield factory
    finally:
        cleanup_database(engine)
        engine.dispose()


def cleanup_database(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("update admin_keys set created_by_admin_action_uuid = null, removed_by_admin_action_uuid = null"))
        connection.execute(text("delete from admin_actions"))
        connection.execute(text("delete from admin_permissions"))
        connection.execute(text("delete from admin_keys"))
        connection.execute(text("delete from issuer_pubkey_history"))
        connection.execute(text("delete from asset_admin_annotations"))
        connection.execute(text("delete from asset_custom_attributes"))
        connection.execute(text("delete from asset_category_tags"))
        connection.execute(text("delete from asset_trading_venues"))
        connection.execute(text("delete from asset_mutable_metadata"))
        connection.execute(text("delete from actions"))
        connection.execute(text("delete from assets"))


def v2_request(
    *,
    asset_id: str = ASSET_ID,
    pubkey: str = PUBKEY,
    ticker: str = "AUDIT",
    domain: str = "audit.example.com",
) -> RegisterAssetRequest:
    return RegisterAssetRequest.model_validate(
        {
            "asset_id": asset_id,
            "contract": {
                "entity": {"domain": domain},
                "initial_issuer_pubkey": pubkey,
                "name": "Audit Asset",
                "precision": 8,
                "ticker": ticker,
                "version": 2,
            },
            "mutable": {
                "category_tags": ["stablecoin"],
                "trading_venues": [{"venue": "sideswap", "url": f"https://sideswap.io/assets/{ticker}"}],
                "custom": {},
            },
        }
    )


def test_genesis_bootstrap_seeds_root_once(session_factory) -> None:
    with session_factory() as session:
        bootstrap_genesis_admin(session, ROOT_PUBKEY)
        bootstrap_genesis_admin(session, ADMIN_PUBKEY)

    with session_factory() as session:
        rows = session.execute(
            text(
                """
                select ak.pubkey, ak.friendly_name, ap.permission
                from admin_keys ak
                join admin_permissions ap on ap.admin_uuid = ak.admin_uuid
                """
            )
        ).mappings().all()

    assert rows == [{"pubkey": ROOT_PUBKEY, "friendly_name": "Genesis Admin", "permission": "root"}]


def test_admin_lifecycle_actions_apply_permissions_and_nonce_idempotency(session_factory) -> None:
    add_action = admin_lifecycle_action(
        "add_admin",
        "nonce-add-admin",
        admin_pubkey=ADMIN_PUBKEY,
        friendly_name="Ops Admin",
        permissions=["annotate_assets"],
    )
    update_action = admin_lifecycle_action(
        "update_admin_permissions",
        "nonce-update-admin",
        timestamp=NOW + timedelta(seconds=1),
        admin_pubkey=ADMIN_PUBKEY,
        permissions=["annotate_assets", "manage_admins"],
    )

    with session_factory() as session:
        bootstrap_genesis_admin(session, ROOT_PUBKEY)
        added = submit_signed_admin_lifecycle(session, add_action)
        retry = submit_signed_admin_lifecycle(session, add_action)
        updated = submit_signed_admin_lifecycle(session, update_action)

    assert added.status == "applied"
    assert retry.status == "idempotent_retry"
    assert retry.audit_entry.audit_id == added.audit_entry.audit_id
    assert updated.audit_entry.audit_id > added.audit_entry.audit_id

    with session_factory() as session:
        permissions = session.execute(
            text(
                """
                select ap.permission
                from admin_permissions ap
                join admin_keys ak on ak.admin_uuid = ap.admin_uuid
                where ak.pubkey = :pubkey
                order by ap.permission
                """
            ),
            {"pubkey": ADMIN_PUBKEY},
        ).scalars().all()

    assert permissions == ["annotate_assets", "manage_admins"]


def test_add_admin_rejects_existing_root_without_mutating_it(session_factory) -> None:
    action = admin_lifecycle_action(
        "add_admin",
        "nonce-replace-root",
        admin_pubkey=ROOT_PUBKEY,
        friendly_name="Replacement Admin",
        permissions=[],
    )

    with session_factory() as session:
        bootstrap_genesis_admin(session, ROOT_PUBKEY)
        with pytest.raises(RegistryError) as exc_info:
            submit_signed_admin_lifecycle(session, action)

        admin = session.execute(
            text("select admin_uuid, friendly_name, status from admin_keys where pubkey = :pubkey"),
            {"pubkey": ROOT_PUBKEY},
        ).mappings().one()
        permissions = session.execute(
            text("select permission from admin_permissions where admin_uuid = :admin_uuid"),
            {"admin_uuid": admin["admin_uuid"]},
        ).scalars().all()
        action_count = session.execute(
            text("select count(*) from admin_actions where nonce = 'nonce-replace-root'")
        ).scalar_one()

    assert exc_info.value.error == "admin_conflict"
    assert exc_info.value.status_code == 409
    assert exc_info.value.details == {"admin_pubkey": ROOT_PUBKEY}
    assert admin["friendly_name"] == "Genesis Admin"
    assert admin["status"] == "active"
    assert permissions == ["root"]
    assert action_count == 0


def test_add_admin_reactivates_removed_admin_with_new_metadata(session_factory) -> None:
    add_action = admin_lifecycle_action(
        "add_admin",
        "nonce-add-removed-admin",
        admin_pubkey=ADMIN_PUBKEY,
        friendly_name="Ops Admin",
        permissions=["annotate_assets"],
    )
    remove_action = admin_lifecycle_action(
        "remove_admin",
        "nonce-remove-admin",
        timestamp=NOW + timedelta(seconds=1),
        admin_pubkey=ADMIN_PUBKEY,
    )
    readd_action = admin_lifecycle_action(
        "add_admin",
        "nonce-readd-admin",
        timestamp=NOW + timedelta(seconds=2),
        admin_pubkey=ADMIN_PUBKEY,
        friendly_name="Reactivated Admin",
        permissions=["manage_admins"],
    )

    with session_factory() as session:
        bootstrap_genesis_admin(session, ROOT_PUBKEY)
        submit_signed_admin_lifecycle(session, add_action)
        submit_signed_admin_lifecycle(session, remove_action)
        readded = submit_signed_admin_lifecycle(session, readd_action)

        admin = session.execute(
            text(
                """
                select admin_uuid, friendly_name, status, removed_by_admin_action_uuid
                from admin_keys
                where pubkey = :pubkey
                """
            ),
            {"pubkey": ADMIN_PUBKEY},
        ).mappings().one()
        permissions = session.execute(
            text("select permission from admin_permissions where admin_uuid = :admin_uuid"),
            {"admin_uuid": admin["admin_uuid"]},
        ).scalars().all()

    assert readded.status == "applied"
    assert admin["friendly_name"] == "Reactivated Admin"
    assert admin["status"] == "active"
    assert admin["removed_by_admin_action_uuid"] is None
    assert permissions == ["manage_admins"]


def test_admin_lifecycle_rejects_removing_last_root(session_factory) -> None:
    with session_factory() as session:
        bootstrap_genesis_admin(session, ROOT_PUBKEY)
        action = admin_lifecycle_action("remove_admin", "nonce-remove-root", admin_pubkey=ROOT_PUBKEY)
        with pytest.raises(RegistryError) as exc_info:
            submit_signed_admin_lifecycle(session, action)

    assert exc_info.value.error == "last_root_admin"


def test_admin_action_requires_signed_actor_pubkey(session_factory) -> None:
    action = admin_lifecycle_action(
        "add_admin",
        "nonce-missing-actor",
        admin_pubkey=ADMIN_PUBKEY,
        friendly_name="Ops Admin",
        permissions=["annotate_assets"],
    )
    unsigned_actor_action = {key: value for key, value in action.items() if key != "actor_pubkey"}

    with session_factory() as session:
        bootstrap_genesis_admin(session, ROOT_PUBKEY)
        with pytest.raises(RegistryError) as exc_info:
            submit_admin_lifecycle_action(
                session,
                payload=payload_for(unsigned_actor_action),
                signature=sign_payload(unsigned_actor_action),
                now=NOW,
            )

    assert exc_info.value.error == "validation_error"


def test_admin_action_rejects_actor_pubkey_signature_mismatch(session_factory) -> None:
    action = admin_lifecycle_action(
        "add_admin",
        "nonce-actor-mismatch",
        actor_pubkey=ADMIN_PUBKEY,
        admin_pubkey=ADMIN_PUBKEY,
        friendly_name="Ops Admin",
        permissions=["annotate_assets"],
    )

    with session_factory() as session:
        bootstrap_genesis_admin(session, ROOT_PUBKEY)
        with pytest.raises(RegistryError) as exc_info:
            submit_signed_admin_lifecycle(session, action, private_key=ROOT_PRIVATE_KEY)

    assert exc_info.value.error == "invalid_signature"


def test_admin_annotation_update_persists_action_and_response_projection(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())
        bootstrap_genesis_admin(session, ROOT_PUBKEY)

    action = annotation_action(
        "nonce-annotation",
        changes={
            "asset_type": "stablecoin",
            "featured": True,
            "malicious": False,
            "delisted": True,
            "admin_notes": "manual review complete",
        },
    )
    with session_factory() as session:
        response = submit_signed_annotation(session, action)

    assert response.admin is not None
    assert response.admin.asset_type == "stablecoin"
    assert response.admin.featured is True
    assert response.admin.delisted is True
    assert response.admin.admin_notes == "manual review complete"
    assert response.admin.last_admin_action is not None
    assert response.admin.last_admin_action.action == "update_admin_annotations"
    assert response.admin.last_admin_action.admin_id is not None

    with session_factory() as session:
        row = session.execute(
            text(
                """
                select act.actor, act.operation, act.admin_id, act.verified_pubkey, act.signature, ann.last_admin_action_uuid
                from actions act
                join asset_admin_annotations ann on ann.last_admin_action_uuid = act.action_uuid
                join assets a on a.asset_uuid = ann.asset_uuid
                where a.asset_id = :asset_id
                """
            ),
            {"asset_id": ASSET_ID},
        ).mappings().one()

    assert row["actor"] == "admin"
    assert row["operation"] == "update_admin_annotations"
    assert row["verified_pubkey"] == ROOT_PUBKEY
    assert row["signature"]
    assert row["last_admin_action_uuid"] is not None


def test_forced_delist_and_relist_require_delist_permission_and_persist_asset_actions(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())
        bootstrap_genesis_admin(session, ROOT_PUBKEY)

    delist = admin_asset_action("force_delist_asset", "nonce-force-delist", reason="policy review")
    relist = admin_asset_action(
        "force_relist_asset",
        "nonce-force-relist",
        timestamp=NOW + timedelta(seconds=1),
        reason="review cleared",
    )
    with session_factory() as session:
        delisted = submit_signed_admin_asset(session, delist)
        relisted = submit_signed_admin_asset(session, relist)

    assert delisted.admin is not None
    assert delisted.admin.delisted is True
    assert delisted.admin.admin_notes == "policy review"
    assert relisted.admin is not None
    assert relisted.admin.delisted is False
    assert relisted.admin.admin_notes == "review cleared"

    with session_factory() as session:
        operations = session.execute(
            text(
                """
                select act.operation
                from actions act
                join assets a on a.asset_uuid = act.asset_uuid
                where a.asset_id = :asset_id and act.actor = 'admin'
                order by act.audit_sequence
                """
            ),
            {"asset_id": ASSET_ID},
        ).scalars().all()

    assert operations == ["force_delist_asset", "force_relist_asset"]


def test_admin_no_op_actions_are_rejected_before_action_insertion(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())
        bootstrap_genesis_admin(session, ROOT_PUBKEY)

        with pytest.raises(RegistryError) as annotation_noop:
            submit_signed_annotation(session, annotation_action("nonce-noop-annotation", changes={"featured": False}))

        with pytest.raises(RegistryError) as lifecycle_noop:
            submit_signed_admin_lifecycle(
                session,
                admin_lifecycle_action(
                    "update_admin_name",
                    "nonce-noop-admin-name",
                    timestamp=NOW + timedelta(seconds=1),
                    admin_pubkey=ROOT_PUBKEY,
                    friendly_name="Genesis Admin",
                ),
            )

        with pytest.raises(RegistryError) as forced_relist_noop:
            submit_signed_admin_asset(
                session,
                admin_asset_action("force_relist_asset", "nonce-noop-relist", timestamp=NOW + timedelta(seconds=2)),
            )

        counts = session.execute(
            text(
                """
                select
                  (select count(*) from actions where nonce in ('nonce-noop-annotation', 'nonce-noop-relist')) as asset_actions,
                  (select count(*) from admin_actions where nonce = 'nonce-noop-admin-name') as admin_actions
                """
            )
        ).mappings().one()

    assert annotation_noop.value.error == "no_op_action"
    assert lifecycle_noop.value.error == "no_op_action"
    assert forced_relist_noop.value.error == "no_op_action"
    assert counts["asset_actions"] == 0
    assert counts["admin_actions"] == 0


def test_audit_projection_orders_paginates_and_filters(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())
        register_v2_asset(session, v2_request(asset_id=ASSET_ID_2, pubkey=PUBKEY_2, ticker="AUDIT2", domain="audit2.example.com"))
        bootstrap_genesis_admin(session, ROOT_PUBKEY)
        admin_lifecycle = submit_signed_admin_lifecycle(
            session,
            admin_lifecycle_action(
                "add_admin",
                "nonce-audit-add-admin",
                admin_pubkey=ADMIN_PUBKEY,
                friendly_name="Audit Admin",
                permissions=["annotate_assets"],
            ),
        )
        submit_signed_annotation(
            session,
            annotation_action(
                "nonce-audit-annotation",
                timestamp=NOW + timedelta(seconds=1),
                changes={"asset_type": "other", "featured": True},
            ),
        )

    with session_factory() as session:
        asset_audit = get_asset_audit_log(session, asset_id=ASSET_ID)
        first_page = search_audit_log(session, limit=1)
        second_page = search_audit_log(session, since_audit_id=first_page.next_since_audit_id or 0, limit=10)
        admin_only = search_audit_log(session, actor="admin", operation="update_admin_annotations")
        lifecycle_only = search_audit_log(session, actor="admin", operation="add_admin")
        descending = search_audit_log(session, order="desc")
        second_asset = get_asset_audit_log(session, asset_id=ASSET_ID_2)

    assert [entry.audit_id for entry in asset_audit.items] == sorted(entry.audit_id for entry in asset_audit.items)
    assert [entry.action["operation"] for entry in asset_audit.items] == ["register", "update_admin_annotations"]
    assert first_page.next_since_audit_id == first_page.items[0].audit_id
    assert all(entry.audit_id > first_page.items[0].audit_id for entry in second_page.items)
    assert len(admin_only.items) == 1
    assert admin_only.items[0].verified_pubkey == ROOT_PUBKEY
    assert [entry.audit_id for entry in lifecycle_only.items] == [admin_lifecycle.audit_entry.audit_id]
    assert lifecycle_only.items[0].action["operation"] == "add_admin"
    assert [entry.audit_id for entry in descending.items] == sorted(
        (entry.audit_id for entry in descending.items),
        reverse=True,
    )
    assert admin_lifecycle.audit_entry.audit_id in {entry.audit_id for entry in descending.items}
    assert [entry.action["asset_id"] for entry in second_asset.items] == [ASSET_ID_2]


def submit_signed_admin_lifecycle(session, action: dict, *, private_key: int = ROOT_PRIVATE_KEY):
    return submit_admin_lifecycle_action(session, payload=payload_for(action), signature=sign_payload(action, private_key), now=NOW)


def submit_signed_annotation(session, action: dict, *, private_key: int = ROOT_PRIVATE_KEY):
    return update_admin_annotations(
        session,
        asset_id=ASSET_ID,
        payload=payload_for(action),
        signature=sign_payload(action, private_key),
        now=NOW,
    )


def submit_signed_admin_asset(session, action: dict, *, private_key: int = ROOT_PRIVATE_KEY):
    return submit_admin_asset_action(
        session,
        asset_id=ASSET_ID,
        payload=payload_for(action),
        signature=sign_payload(action, private_key),
        now=NOW,
    )


def admin_lifecycle_action(operation: str, nonce: str, *, timestamp: datetime = NOW, **extra) -> dict:
    return {
        "signing_context": "liquid-asset-registry-admin-action-v1",
        "actor_pubkey": ROOT_PUBKEY,
        "operation": operation,
        "timestamp": iso(timestamp),
        "nonce": nonce,
        **extra,
    }


def annotation_action(nonce: str, *, timestamp: datetime = NOW, changes: dict) -> dict:
    return {
        "signing_context": "liquid-asset-registry-admin-action-v1",
        "actor_pubkey": ROOT_PUBKEY,
        "operation": "update_admin_annotations",
        "asset_id": ASSET_ID,
        "timestamp": iso(timestamp),
        "nonce": nonce,
        "changes": changes,
    }


def admin_asset_action(operation: str, nonce: str, *, timestamp: datetime = NOW, **extra) -> dict:
    return {
        "signing_context": "liquid-asset-registry-admin-action-v1",
        "actor_pubkey": ROOT_PUBKEY,
        "operation": operation,
        "asset_id": ASSET_ID,
        "timestamp": iso(timestamp),
        "nonce": nonce,
        **extra,
    }


def payload_for(action: dict) -> bytes:
    return canonical_json(action).encode("utf-8")


def sign_payload(action: dict, private_key: int = ROOT_PRIVATE_KEY) -> str:
    payload = payload_for(action)
    message_hash = _bitcoin_signed_message_hash(payload.decode("utf-8"))
    key_bytes = private_key.to_bytes(wally.EC_PRIVATE_KEY_LEN, "big")
    signature = wally.ec_sig_from_bytes(key_bytes, message_hash, wally.EC_FLAG_ECDSA)
    return base64.b64encode(signature).decode()


def pubkey_for_private_key(private_key: int) -> str:
    return wally.ec_public_key_from_private_key(private_key.to_bytes(wally.EC_PRIVATE_KEY_LEN, "big")).hex()


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
