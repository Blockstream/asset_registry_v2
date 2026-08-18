import base64
import hashlib
import importlib
import io
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
import wallycore as wally
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import load_only, sessionmaker

from registry_api.admin_actions import bootstrap_genesis_admin
from registry_api.canonical_json import canonical_json
from registry_api.db import get_db
from registry_api.errors import RegistryError
from registry_api.icons import (
    decide_icon_proposal,
    decode_and_validate_new_icon,
    icon_map,
    published_icon_for_asset,
    published_icon_for_asset_by_hash,
    search_issuer_icon_proposals,
    search_pending_icon_proposals,
    set_admin_asset_icon,
    submit_icon_proposal,
)
from registry_api.issuer_actions import get_latest_action_hash, submit_issuer_action
from registry_api.legacy_assets import (
    deregister_legacy_asset,
    get_legacy_asset,
    list_legacy_assets_json_bytes,
)
from registry_api.legacy_icon_import import import_legacy_icons
from registry_api.main import create_app
from registry_api.models import (
    Action,
    AdminKey,
    AdminPermission,
    Asset,
    AssetIconProposal,
)
from registry_api.registration import register_legacy_asset
from registry_api.schemas import (
    LegacyAssetRequest,
    ProposeIconAction,
    RegisterAssetRequest,
    SetIconAction,
)
from registry_api.signatures import _bitcoin_signed_message_hash
from registry_api.v2_assets import get_v2_asset, register_v2_asset


pytestmark = pytest.mark.skipif(
    not os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL"),
    reason="ASSET_REGISTRY_TEST_DATABASE_URL is required for icon tests",
)

ASSET_ID = "039015fa82bfd416c12ad9399e3b77bccd4d19fcf0d872b5815480eb26d3bc46"
NOW = datetime(2026, 7, 17, 16, 0, tzinfo=UTC)
ISSUER_PRIVATE_KEY = 1
ADMIN_PRIVATE_KEY = 2
LIMITED_ADMIN_PRIVATE_KEY = 3
ISSUER_PUBKEY = wally.ec_public_key_from_private_key(
    ISSUER_PRIVATE_KEY.to_bytes(32, "big")
).hex()
ADMIN_PUBKEY = wally.ec_public_key_from_private_key(
    ADMIN_PRIVATE_KEY.to_bytes(32, "big")
).hex()
LIMITED_ADMIN_PUBKEY = wally.ec_public_key_from_private_key(
    LIMITED_ADMIN_PRIVATE_KEY.to_bytes(32, "big")
).hex()


@pytest.fixture()
def session_factory():
    engine = create_engine(os.environ["ASSET_REGISTRY_TEST_DATABASE_URL"])
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    cleanup_database(engine)
    try:
        yield factory
    finally:
        cleanup_database(engine)
        engine.dispose()


@pytest.fixture()
def registered_asset(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, registration_request())
        bootstrap_genesis_admin(session, ADMIN_PUBKEY)


def cleanup_database(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("delete from asset_icon_proposals"))
        connection.execute(
            text(
                "update admin_keys set created_by_admin_action_uuid = null, removed_by_admin_action_uuid = null"
            )
        )
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


def registration_request() -> RegisterAssetRequest:
    return RegisterAssetRequest.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "icons.example.com"},
                "initial_issuer_pubkey": ISSUER_PUBKEY,
                "name": "Icon Asset",
                "precision": 8,
                "ticker": "ICON",
                "version": 2,
            },
        }
    )


def legacy_registration_request_with_contract_icon() -> LegacyAssetRequest:
    return LegacyAssetRequest.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "icons.example.com"},
                "issuer_pubkey": ISSUER_PUBKEY,
                "name": "Icon Asset",
                "precision": 8,
                "ticker": "ICON",
                "version": 0,
                "icon": "https://icons.example.com/contract-icon.png",
            },
        }
    )


def png_bytes(
    *,
    size: tuple[int, int] = (500, 500),
    alpha: bool = True,
    color: tuple[int, ...] | None = None,
) -> bytes:
    mode = "RGBA" if alpha else "RGB"
    image_color = color or ((40, 80, 120, 0) if alpha else (40, 80, 120))
    output = io.BytesIO()
    Image.new(mode, size, image_color).save(output, format="PNG", optimize=True)
    return output.getvalue()


def icon_href(icon_hash: str, *, asset_id: str = ASSET_ID) -> str:
    return f"/v2/assets/{asset_id}/icon/{icon_hash}.png"


def proposal_action(
    session, image_data: bytes, *, nonce: str = "nonce-icon-proposal", timestamp=NOW
) -> dict:
    latest = get_latest_action_hash(session, ASSET_ID)
    return {
        "signing_context": "liquid-asset-registry-action-v1",
        "asset_id": ASSET_ID,
        "operation": "propose_icon",
        "icon_hash": hashlib.sha256(image_data).hexdigest(),
        "prev_action_hash": latest.action_hash,
        "timestamp": iso(timestamp),
        "nonce": nonce,
    }


def submit_proposal(
    session, image_data: bytes, *, nonce: str = "nonce-icon-proposal", timestamp=NOW
):
    action_dict = proposal_action(session, image_data, nonce=nonce, timestamp=timestamp)
    action = ProposeIconAction.model_validate(action_dict)
    return submit_icon_proposal(
        session,
        asset_id=ASSET_ID,
        action=action,
        icon=base64.b64encode(image_data).decode(),
        signature=sign(action_dict, ISSUER_PRIVATE_KEY),
        now=timestamp,
    )


def admin_action(
    operation: str, icon_hash: str, nonce: str, *, timestamp=NOW, **extra
) -> dict:
    return {
        "signing_context": "liquid-asset-registry-admin-action-v1",
        "actor_pubkey": ADMIN_PUBKEY,
        "operation": operation,
        "asset_id": ASSET_ID,
        "icon_hash": icon_hash,
        "timestamp": iso(timestamp),
        "nonce": nonce,
        **extra,
    }


def test_new_icon_validation_enforces_objective_rules() -> None:
    valid = png_bytes()
    decoded, digest = decode_and_validate_new_icon(base64.b64encode(valid).decode())
    assert decoded == valid
    assert digest == hashlib.sha256(valid).hexdigest()

    with pytest.raises(RegistryError, match="500x500"):
        decode_and_validate_new_icon(
            base64.b64encode(png_bytes(size=(499, 500))).decode()
        )
    with pytest.raises(RegistryError, match="alpha channel"):
        decode_and_validate_new_icon(base64.b64encode(png_bytes(alpha=False)).decode())


def test_legacy_responses_preserve_contract_icon_without_embedding_approved_icon(
    session_factory,
) -> None:
    image_data = png_bytes()
    encoded = base64.b64encode(image_data).decode()
    with session_factory() as session:
        register_legacy_asset(session, legacy_registration_request_with_contract_icon())
        import_legacy_icons(session, {ASSET_ID: encoded})

    with session_factory() as session:
        lookup = get_legacy_asset(session, ASSET_ID)
        listing = json.loads(list_legacy_assets_json_bytes(session))
        v2_asset = get_v2_asset(session, ASSET_ID)

        assert "icon" not in lookup
        assert (
            lookup["contract"]["icon"] == "https://icons.example.com/contract-icon.png"
        )
        assert listing[ASSET_ID] == lookup
        assert v2_asset.icon is not None
        assert v2_asset.icon.href == icon_href(hashlib.sha256(image_data).hexdigest())
        assert icon_map(session) == {ASSET_ID: encoded}


def test_legacy_icon_cache_migration_preserves_contract_and_v2_data(
    session_factory,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.0011_remove_legacy_response_icons"
    )
    image_data = png_bytes()
    encoded = base64.b64encode(image_data).decode()
    stale_legacy_json = {
        "asset_id": ASSET_ID,
        "contract": {"icon": "contract-icon"},
        "icon": "stale-approved-icon",
    }

    with session_factory() as session:
        register_legacy_asset(session, legacy_registration_request_with_contract_icon())
        import_legacy_icons(session, {ASSET_ID: encoded})
        original_v2_json = session.execute(
            text(
                """
                select fragments.v2_json
                from asset_serialized_fragments as fragments
                join assets on assets.asset_uuid = fragments.asset_uuid
                where assets.asset_id = :asset_id
                """
            ),
            {"asset_id": ASSET_ID},
        ).scalar_one()
        session.execute(
            text(
                """
                update asset_serialized_fragments as fragments
                set legacy_json = :legacy_json
                from assets
                where assets.asset_uuid = fragments.asset_uuid
                  and assets.asset_id = :asset_id
                """
            ),
            {
                "asset_id": ASSET_ID,
                "legacy_json": json.dumps(stale_legacy_json),
            },
        )

        session.execute(migration.REMOVE_LEGACY_RESPONSE_ICONS)
        cleaned_legacy_json, cleaned_v2_json = session.execute(
            text(
                """
                select fragments.legacy_json, fragments.v2_json
                from asset_serialized_fragments as fragments
                join assets on assets.asset_uuid = fragments.asset_uuid
                where assets.asset_id = :asset_id
                """
            ),
            {"asset_id": ASSET_ID},
        ).one()

        assert json.loads(cleaned_legacy_json) == {
            "asset_id": ASSET_ID,
            "contract": {"icon": "contract-icon"},
        }
        assert cleaned_v2_json == original_v2_json

        session.execute(migration.RESTORE_LEGACY_RESPONSE_ICONS)
        restored_legacy_json = session.execute(
            text(
                """
                select fragments.legacy_json
                from asset_serialized_fragments as fragments
                join assets on assets.asset_uuid = fragments.asset_uuid
                where assets.asset_id = :asset_id
                """
            ),
            {"asset_id": ASSET_ID},
        ).scalar_one()
        assert json.loads(restored_legacy_json) == {
            "asset_id": ASSET_ID,
            "contract": {"icon": "contract-icon"},
            "icon": encoded,
        }


def test_v2_icon_cache_migration_replaces_and_restores_embedded_bytes(
    session_factory,
) -> None:
    migration = importlib.import_module("migrations.versions.0012_v2_icon_descriptors")
    image_data = png_bytes()
    icon_hash = hashlib.sha256(image_data).hexdigest()
    encoded = base64.b64encode(image_data).decode()
    old_v2_json = {
        "asset_id": ASSET_ID,
        "contract": {"name": "preserved"},
        "icon": encoded,
    }

    with session_factory() as session:
        register_legacy_asset(session, legacy_registration_request_with_contract_icon())
        import_legacy_icons(session, {ASSET_ID: encoded})
        session.execute(
            text(
                """
                update asset_serialized_fragments as fragments
                set v2_json = :v2_json
                from assets
                where assets.asset_uuid = fragments.asset_uuid
                  and assets.asset_id = :asset_id
                """
            ),
            {"asset_id": ASSET_ID, "v2_json": json.dumps(old_v2_json)},
        )

        session.execute(migration.ADD_V2_ICON_DESCRIPTORS)
        descriptor_json = session.execute(
            text(
                """
                select fragments.v2_json
                from asset_serialized_fragments as fragments
                join assets on assets.asset_uuid = fragments.asset_uuid
                where assets.asset_id = :asset_id
                """
            ),
            {"asset_id": ASSET_ID},
        ).scalar_one()
        assert json.loads(descriptor_json) == {
            "asset_id": ASSET_ID,
            "contract": {"name": "preserved"},
            "icon": {"href": icon_href(icon_hash)},
        }

        session.execute(migration.RESTORE_V2_BASE64_ICONS)
        restored_json = session.execute(
            text(
                """
                select fragments.v2_json
                from asset_serialized_fragments as fragments
                join assets on assets.asset_uuid = fragments.asset_uuid
                where assets.asset_id = :asset_id
                """
            ),
            {"asset_id": ASSET_ID},
        ).scalar_one()
        assert json.loads(restored_json) == old_v2_json


def test_content_addressed_icon_endpoints_and_cache_headers(
    session_factory, registered_asset
) -> None:
    image_data = png_bytes()
    with session_factory() as session:
        proposal = submit_proposal(session, image_data)
        approval = admin_action(
            "approve_icon",
            proposal.proposal.icon_hash,
            "nonce-approve-for-content-endpoint",
        )
        decide_icon_proposal(
            session,
            asset_id=ASSET_ID,
            payload=canonical_json(approval).encode(),
            signature=sign(approval, ADMIN_PRIVATE_KEY),
            now=NOW,
        )

        app = create_app()
        app.dependency_overrides[get_db] = lambda: session
        client = TestClient(app)

        current = client.get(
            f"/v2/assets/{ASSET_ID}/icon",
            follow_redirects=False,
        )
        expected_href = icon_href(proposal.proposal.icon_hash)
        assert current.status_code == 307
        assert current.headers["location"] == expected_href
        assert current.headers["cache-control"] == "no-cache"

        icon_response = client.get(expected_href)
        assert icon_response.status_code == 200
        assert icon_response.content == image_data
        assert icon_response.headers["content-type"] == "image/png"
        assert "content-encoding" not in icon_response.headers
        assert icon_response.headers["x-content-type-options"] == "nosniff"
        assert (
            icon_response.headers["cache-control"]
            == "public, max-age=31536000, immutable"
        )
        assert icon_response.headers["etag"] == f'"{proposal.proposal.icon_hash}"'

        not_modified = client.get(
            expected_href,
            headers={"If-None-Match": icon_response.headers["etag"]},
        )
        assert not_modified.status_code == 304
        assert not not_modified.content
        assert not_modified.headers["etag"] == icon_response.headers["etag"]
        assert not_modified.headers["x-content-type-options"] == "nosniff"

        weak_list_match = client.get(
            expected_href,
            headers={
                "If-None-Match": f'"unrelated", W/{icon_response.headers["etag"]}'
            },
        )
        assert weak_list_match.status_code == 304
        assert not weak_list_match.content

        wildcard_match = client.get(
            expected_href,
            headers={"If-None-Match": "*"},
        )
        assert wildcard_match.status_code == 304
        assert not wildcard_match.content

        wrong_hash = "0" * 64
        mismatch = client.get(icon_href(wrong_hash))
        assert mismatch.status_code == 404
        assert mismatch.json()["error"] == "icon_not_found"

        missing_wildcard_match = client.get(
            icon_href(wrong_hash),
            headers={"If-None-Match": "*"},
        )
        assert missing_wildcard_match.status_code == 404
        assert missing_wildcard_match.json()["error"] == "icon_not_found"


def test_wrong_icon_hash_does_not_load_current_image_bytes(
    session_factory, registered_asset
) -> None:
    image_data = png_bytes()
    with session_factory() as session:
        proposal = submit_proposal(session, image_data)
        approval = admin_action(
            "approve_icon",
            proposal.proposal.icon_hash,
            "nonce-approve-for-wrong-hash-load-test",
        )
        decide_icon_proposal(
            session,
            asset_id=ASSET_ID,
            payload=canonical_json(approval).encode(),
            signature=sign(approval, ADMIN_PRIVATE_KEY),
            now=NOW,
        )

    with session_factory() as session:
        stored = session.scalar(
            select(AssetIconProposal).options(
                load_only(AssetIconProposal.icon_proposal_uuid)
            )
        )
        assert stored is not None
        assert "image_data" in inspect(stored).unloaded

        with pytest.raises(RegistryError) as exc_info:
            published_icon_for_asset_by_hash(session, ASSET_ID, "0" * 64)

        assert exc_info.value.error == "icon_not_found"
        assert "image_data" in inspect(stored).unloaded


def test_current_icon_endpoint_returns_icon_not_found_without_approved_icon(
    session_factory, registered_asset
) -> None:
    with session_factory() as session:
        app = create_app()
        app.dependency_overrides[get_db] = lambda: session
        response = TestClient(app).get(
            f"/v2/assets/{ASSET_ID}/icon",
            follow_redirects=False,
        )

    assert response.status_code == 404
    assert response.json()["error"] == "icon_not_found"


def test_icon_descriptors_do_not_load_image_bytes(
    session_factory, registered_asset
) -> None:
    image_data = png_bytes()
    with session_factory() as session:
        proposal = submit_proposal(session, image_data)
        approval = admin_action(
            "approve_icon",
            proposal.proposal.icon_hash,
            "nonce-approve-for-deferred-image-test",
        )
        decide_icon_proposal(
            session,
            asset_id=ASSET_ID,
            payload=canonical_json(approval).encode(),
            signature=sign(approval, ADMIN_PRIVATE_KEY),
            now=NOW,
        )

        session.expunge_all()
        published = published_icon_for_asset(
            session, ASSET_ID, include_image_data=False
        )
        assert published.icon_hash == proposal.proposal.icon_hash
        assert "image_data" in inspect(published).unloaded

        session.expunge_all()
        asset = session.scalar(select(Asset).where(Asset.asset_id == ASSET_ID))
        assert asset is not None
        response = get_v2_asset(session, ASSET_ID)
        assert response.icon is not None
        assert asset.icon is not None
        assert "image_data" in inspect(asset.icon).unloaded


def test_submit_search_approve_publish_and_reject_duplicate(
    session_factory, registered_asset
) -> None:
    image_data = png_bytes()
    with session_factory() as session:
        response = submit_proposal(session, image_data)
        assert response.status == "applied"
        assert (
            response.audit_entry.action["icon_hash"]
            == hashlib.sha256(image_data).hexdigest()
        )
        assert "icon" not in response.audit_entry.action

        with pytest.raises(RegistryError) as pending_error:
            submit_proposal(
                session,
                png_bytes(),
                nonce="nonce-second-proposal",
                timestamp=NOW + timedelta(seconds=1),
            )
        assert pending_error.value.error == "icon_pending_conflict"

        query = {
            "actor_pubkey": ADMIN_PUBKEY,
            "operation": "list_pending_icon_proposals",
            "order": "asc",
            "page": 1,
            "page_size": 20,
            "signing_context": "liquid-asset-registry-admin-query-v1",
            "timestamp": iso(NOW),
        }
        pending = search_pending_icon_proposals(
            session,
            payload=canonical_json(query).encode(),
            signature=sign(query, ADMIN_PRIVATE_KEY),
            now=NOW,
        )
        assert pending.total_count == 1
        assert base64.b64decode(pending.items[0].icon) == image_data

        approve = admin_action(
            "approve_icon", response.proposal.icon_hash, "nonce-approve-icon"
        )
        approved_asset = decide_icon_proposal(
            session,
            asset_id=ASSET_ID,
            payload=canonical_json(approve).encode(),
            signature=sign(approve, ADMIN_PRIVATE_KEY),
            now=NOW,
        )
        assert approved_asset.icon is not None
        assert approved_asset.icon.href == icon_href(response.proposal.icon_hash)
        assert icon_map(session) == {ASSET_ID: base64.b64encode(image_data).decode()}
        assert get_v2_asset(session, ASSET_ID).icon == approved_asset.icon

        with pytest.raises(RegistryError) as duplicate_error:
            decide_icon_proposal(
                session,
                asset_id=ASSET_ID,
                payload=canonical_json(approve).encode(),
                signature=sign(approve, ADMIN_PRIVATE_KEY),
                now=NOW,
            )
        assert duplicate_error.value.error == "icon_proposal_already_decided"


def test_hash_mismatch_includes_expected_hash(
    session_factory, registered_asset
) -> None:
    image_data = png_bytes()
    with session_factory() as session:
        action_dict = proposal_action(session, image_data)
        action_dict["icon_hash"] = "00" * 32
        action = ProposeIconAction.model_validate(action_dict)
        with pytest.raises(RegistryError) as error:
            submit_icon_proposal(
                session,
                asset_id=ASSET_ID,
                action=action,
                icon=base64.b64encode(image_data).decode(),
                signature=sign(action_dict, ISSUER_PRIVATE_KEY),
                now=NOW,
            )
        assert error.value.error == "icon_hash_mismatch"
        assert error.value.details == {
            "expected_icon_hash": hashlib.sha256(image_data).hexdigest(),
            "submitted_icon_hash": "00" * 32,
        }


def test_rejection_clears_bytes_and_keeps_audit_metadata(
    session_factory, registered_asset
) -> None:
    image_data = png_bytes()
    with session_factory() as session:
        response = submit_proposal(session, image_data)
        reject = admin_action(
            "reject_icon",
            response.proposal.icon_hash,
            "nonce-reject-icon",
            reason="not circular",
        )
        rejected_asset = decide_icon_proposal(
            session,
            asset_id=ASSET_ID,
            payload=canonical_json(reject).encode(),
            signature=sign(reject, ADMIN_PRIVATE_KEY),
            now=NOW,
        )
        assert rejected_asset.icon is None
        proposal = session.scalar(select(AssetIconProposal))
        assert proposal is not None
        assert proposal.status == "rejected"
        assert proposal.image_data is None
        assert proposal.icon_hash == response.proposal.icon_hash


def test_admin_can_upload_assign_and_reuse_approved_icon(
    session_factory, registered_asset
) -> None:
    first_image = png_bytes()
    second_image = png_bytes(color=(120, 80, 40, 0))
    with session_factory() as session:
        first_action = admin_action(
            "set_icon",
            hashlib.sha256(first_image).hexdigest(),
            "nonce-admin-set-first",
        )
        first_asset = set_admin_asset_icon(
            session,
            asset_id=ASSET_ID,
            action=SetIconAction.model_validate(first_action),
            icon=base64.b64encode(first_image).decode(),
            signature=sign(first_action, ADMIN_PRIVATE_KEY),
            now=NOW,
        )
        assert first_asset.icon is not None
        first_href = first_asset.icon.href

        second_action = admin_action(
            "set_icon",
            hashlib.sha256(second_image).hexdigest(),
            "nonce-admin-set-second",
            timestamp=NOW + timedelta(seconds=1),
        )
        second_asset = set_admin_asset_icon(
            session,
            asset_id=ASSET_ID,
            action=SetIconAction.model_validate(second_action),
            icon=base64.b64encode(second_image).decode(),
            signature=sign(second_action, ADMIN_PRIVATE_KEY),
            now=NOW + timedelta(seconds=1),
        )
        assert second_asset.icon is not None
        second_href = second_asset.icon.href
        assert second_href != first_href

        reuse_action = admin_action(
            "set_icon",
            hashlib.sha256(first_image).hexdigest(),
            "nonce-admin-reuse-first",
            timestamp=NOW + timedelta(seconds=2),
        )
        reused_asset = set_admin_asset_icon(
            session,
            asset_id=ASSET_ID,
            action=SetIconAction.model_validate(reuse_action),
            icon=base64.b64encode(first_image).decode(),
            signature=sign(reuse_action, ADMIN_PRIVATE_KEY),
            now=NOW + timedelta(seconds=2),
        )
        proposals = session.scalars(
            select(AssetIconProposal).order_by(AssetIconProposal.proposed_at)
        ).all()
        set_actions = session.scalars(
            select(Action)
            .where(Action.operation == "set_icon")
            .order_by(Action.audit_sequence)
        ).all()
        assert len(proposals) == 2
        assert all(
            proposal.status == "approved" and proposal.image_data is not None
            for proposal in proposals
        )
        assert all(
            proposal.submission_method == "admin_upload" for proposal in proposals
        )
        assert len(set_actions) == 3
        assert all("icon" not in action.action for action in set_actions)
        assert reused_asset.icon is not None
        assert reused_asset.icon.href == first_href
        asset = session.scalar(select(Asset).where(Asset.asset_id == ASSET_ID))
        assert asset is not None
        assert asset.active_icon_proposal_uuid == proposals[0].icon_proposal_uuid


def test_admin_icon_upload_requires_manage_icons(
    session_factory, registered_asset
) -> None:
    image_data = png_bytes()
    with session_factory() as session:
        limited_admin = AdminKey(
            pubkey=LIMITED_ADMIN_PUBKEY,
            friendly_name="Reviewer only",
            status="active",
        )
        session.add(limited_admin)
        session.flush()
        session.add(
            AdminPermission(
                admin_uuid=limited_admin.admin_uuid,
                permission="review_icons",
            )
        )
        session.commit()

        action = {
            **admin_action(
                "set_icon",
                hashlib.sha256(image_data).hexdigest(),
                "nonce-limited-admin-set",
            ),
            "actor_pubkey": LIMITED_ADMIN_PUBKEY,
        }
        with pytest.raises(RegistryError) as error:
            set_admin_asset_icon(
                session,
                asset_id=ASSET_ID,
                action=SetIconAction.model_validate(action),
                icon=base64.b64encode(image_data).decode(),
                signature=sign(action, LIMITED_ADMIN_PRIVATE_KEY),
                now=NOW,
            )
        assert error.value.error == "forbidden"
        assert "manage_icons" in error.value.message


def test_admin_upload_approves_identical_pending_proposal_in_place(
    session_factory, registered_asset
) -> None:
    image_data = png_bytes()
    with session_factory() as session:
        pending = submit_proposal(session, image_data)
        action = admin_action(
            "set_icon",
            pending.proposal.icon_hash,
            "nonce-admin-set-pending",
        )
        asset_response = set_admin_asset_icon(
            session,
            asset_id=ASSET_ID,
            action=SetIconAction.model_validate(action),
            icon=base64.b64encode(image_data).decode(),
            signature=sign(action, ADMIN_PRIVATE_KEY),
            now=NOW,
        )

        proposals = session.scalars(select(AssetIconProposal)).all()
        assert len(proposals) == 1
        assert proposals[0].status == "approved"
        assert proposals[0].submission_method == "v2_issuer_signature"
        assert proposals[0].decided_by_action is not None
        assert proposals[0].decided_by_action.operation == "set_icon"
        assert asset_response.icon is not None
        assert asset_response.icon.href == icon_href(action["icon_hash"])


def test_issuer_can_list_only_proposals_made_by_its_key(
    session_factory, registered_asset
) -> None:
    image_data = png_bytes()
    with session_factory() as session:
        submit_proposal(session, image_data)
        query = {
            "actor_pubkey": ISSUER_PUBKEY,
            "asset_id": ASSET_ID,
            "operation": "list_icon_proposals",
            "order": "desc",
            "page": 1,
            "page_size": 20,
            "signing_context": "liquid-asset-registry-issuer-query-v1",
            "timestamp": iso(NOW),
        }
        proposals = search_issuer_icon_proposals(
            session,
            asset_id=ASSET_ID,
            payload=canonical_json(query).encode(),
            signature=sign(query, ISSUER_PRIVATE_KEY),
            now=NOW,
        )
        assert proposals.total_count == 1
        assert base64.b64decode(proposals.items[0].icon or "") == image_data

        unauthorized_query = {**query, "actor_pubkey": ADMIN_PUBKEY}
        with pytest.raises(RegistryError) as error:
            search_issuer_icon_proposals(
                session,
                asset_id=ASSET_ID,
                payload=canonical_json(unauthorized_query).encode(),
                signature=sign(unauthorized_query, ADMIN_PRIVATE_KEY),
                now=NOW,
            )
        assert error.value.error == "forbidden"


def test_approving_replacement_retains_previous_approved_bytes(
    session_factory, registered_asset
) -> None:
    first_image = png_bytes()
    second_image = png_bytes(color=(120, 80, 40, 0))
    with session_factory() as session:
        first = submit_proposal(session, first_image)
        first_approval = admin_action(
            "approve_icon",
            first.proposal.icon_hash,
            "nonce-approve-first",
        )
        decide_icon_proposal(
            session,
            asset_id=ASSET_ID,
            payload=canonical_json(first_approval).encode(),
            signature=sign(first_approval, ADMIN_PRIVATE_KEY),
            now=NOW,
        )

        second = submit_proposal(
            session,
            second_image,
            nonce="nonce-second-icon",
            timestamp=NOW + timedelta(seconds=1),
        )
        second_approval = admin_action(
            "approve_icon",
            second.proposal.icon_hash,
            "nonce-approve-second",
            timestamp=NOW + timedelta(seconds=1),
        )
        approved_asset = decide_icon_proposal(
            session,
            asset_id=ASSET_ID,
            payload=canonical_json(second_approval).encode(),
            signature=sign(second_approval, ADMIN_PRIVATE_KEY),
            now=NOW + timedelta(seconds=1),
        )

        asset = session.scalar(select(Asset).where(Asset.asset_id == ASSET_ID))
        proposals = session.scalars(
            select(AssetIconProposal).order_by(AssetIconProposal.proposed_at)
        ).all()
        assert asset is not None
        assert len(proposals) == 2
        assert [proposal.image_data for proposal in proposals] == [
            first_image,
            second_image,
        ]
        assert asset.active_icon_proposal_uuid == proposals[1].icon_proposal_uuid
        assert approved_asset.icon is not None
        assert approved_asset.icon.href == icon_href(second.proposal.icon_hash)

        app = create_app()
        app.dependency_overrides[get_db] = lambda: session
        client = TestClient(app)
        first_response = client.get(icon_href(first.proposal.icon_hash))
        second_response = client.get(icon_href(second.proposal.icon_hash))
        assert first_response.status_code == 200
        assert first_response.content == first_image
        assert second_response.status_code == 200
        assert second_response.content == second_image


def test_deregister_obsoletes_all_proposals_and_clears_active_icon(
    session_factory, registered_asset
) -> None:
    image_data = png_bytes()
    with session_factory() as session:
        proposal = submit_proposal(session, image_data)
        approval = admin_action(
            "approve_icon",
            proposal.proposal.icon_hash,
            "nonce-approve-before-delete",
        )
        decide_icon_proposal(
            session,
            asset_id=ASSET_ID,
            payload=canonical_json(approval).encode(),
            signature=sign(approval, ADMIN_PRIVATE_KEY),
            now=NOW,
        )
        latest = get_latest_action_hash(session, ASSET_ID)
        deregister = {
            "signing_context": "liquid-asset-registry-action-v1",
            "asset_id": ASSET_ID,
            "operation": "deregister",
            "prev_action_hash": latest.action_hash,
            "timestamp": iso(NOW + timedelta(seconds=1)),
            "nonce": "nonce-deregister-icon",
        }
        response = submit_issuer_action(
            session,
            asset_id=ASSET_ID,
            payload=canonical_json(deregister).encode(),
            signature=sign(deregister, ISSUER_PRIVATE_KEY),
            now=NOW + timedelta(seconds=1),
        )

        asset = session.scalar(select(Asset).where(Asset.asset_id == ASSET_ID))
        stored = session.scalar(select(AssetIconProposal))
        deregister_action_row = session.scalar(
            select(Action).where(Action.audit_sequence == response.audit_entry.audit_id)
        )
        assert response.asset is not None
        assert response.asset.icon is None
        assert asset is not None
        assert asset.active_icon_proposal_uuid is None
        assert stored is not None
        assert deregister_action_row is not None
        assert stored.obsoleted_at is not None
        assert stored.obsoleted_by_action_uuid == deregister_action_row.action_uuid
        assert stored.image_data == image_data
        assert icon_map(session) == {}


def test_legacy_deregister_also_obsoletes_icon_history(
    session_factory, registered_asset
) -> None:
    image_data = png_bytes()
    with session_factory() as session:
        proposal = submit_proposal(session, image_data)
        approval = admin_action(
            "approve_icon",
            proposal.proposal.icon_hash,
            "nonce-approve-before-legacy-delete",
        )
        decide_icon_proposal(
            session,
            asset_id=ASSET_ID,
            payload=canonical_json(approval).encode(),
            signature=sign(approval, ADMIN_PRIVATE_KEY),
            now=NOW,
        )
        message_hash = _bitcoin_signed_message_hash(f"remove {ASSET_ID} from registry")
        deletion_signature = base64.b64encode(
            wally.ec_sig_from_bytes(
                ISSUER_PRIVATE_KEY.to_bytes(32, "big"),
                message_hash,
                wally.EC_FLAG_ECDSA,
            )
        ).decode()
        assert (
            deregister_legacy_asset(session, ASSET_ID, deletion_signature)
            == "Asset deleted"
        )

        asset = session.scalar(select(Asset).where(Asset.asset_id == ASSET_ID))
        stored = session.scalar(select(AssetIconProposal))
        assert asset is not None
        assert asset.active_icon_proposal_uuid is None
        assert stored is not None
        assert stored.obsoleted_at is not None
        assert stored.obsoleted_by_action_uuid is not None
        assert stored.image_data == image_data


def test_legacy_import_grandfathers_and_is_idempotent(
    session_factory, registered_asset
) -> None:
    legacy_image = png_bytes(size=(537, 537), alpha=False)
    encoded = base64.b64encode(legacy_image).decode()
    with session_factory() as session:
        dry_run = import_legacy_icons(session, {ASSET_ID: encoded}, dry_run=True)
        assert dry_run["would_import_count"] == 1
        assert dry_run["grandfathered_deviations"] == {
            "dimensions": 1,
            "size": 0,
            "alpha": 1,
        }

        imported = import_legacy_icons(session, {ASSET_ID: encoded})
        assert imported["imported_count"] == 1
        asset_response = get_v2_asset(session, ASSET_ID)
        assert asset_response.icon is not None
        assert asset_response.icon.href == icon_href(
            hashlib.sha256(legacy_image).hexdigest()
        )

        retry = import_legacy_icons(session, {ASSET_ID: encoded})
        assert retry["imported_count"] == 0
        assert retry["skipped_existing_count"] == 1


def sign(value: dict, private_key: int) -> str:
    canonical = canonical_json(value)
    digest = _bitcoin_signed_message_hash(canonical)
    signature = wally.ec_sig_from_bytes(
        private_key.to_bytes(32, "big"), digest, wally.EC_FLAG_ECDSA
    )
    return base64.b64encode(signature).decode()


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
