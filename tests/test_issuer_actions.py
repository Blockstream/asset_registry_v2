import base64
import os
from datetime import UTC, datetime, timedelta

import pytest
import wallycore as wally
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from registry_api.canonical_json import canonical_json
from registry_api.errors import RegistryError
from registry_api.issuer_actions import get_latest_action_hash, submit_issuer_action
from registry_api.registration import register_legacy_asset
from registry_api.schemas import LegacyAssetRequest, RegisterAssetRequest
from registry_api.signatures import _bitcoin_signed_message_hash
from registry_api.v2_assets import register_v2_asset


pytestmark = pytest.mark.skipif(
    not os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL"),
    reason="ASSET_REGISTRY_TEST_DATABASE_URL is required for issuer action tests",
)

ASSET_ID = "fa909f1b00000000000000000000000000000000000000000000000000000000"
NOW = datetime(2026, 4, 29, 16, 0, tzinfo=UTC)
PRIVATE_KEY = 1
NEW_PRIVATE_KEY = 2
PUBKEY = ""
NEW_PUBKEY = ""


@pytest.fixture(scope="module", autouse=True)
def compute_pubkeys() -> None:
    global PUBKEY, NEW_PUBKEY
    PUBKEY = pubkey_for_private_key(PRIVATE_KEY)
    NEW_PUBKEY = pubkey_for_private_key(NEW_PRIVATE_KEY)


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
        register_v2_asset(session, v2_request())


def cleanup_database(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("delete from issuer_pubkey_history"))
        connection.execute(text("delete from asset_admin_annotations"))
        connection.execute(text("delete from asset_custom_attributes"))
        connection.execute(text("delete from asset_category_tags"))
        connection.execute(text("delete from asset_trading_venues"))
        connection.execute(text("delete from asset_mutable_metadata"))
        connection.execute(text("delete from actions"))
        connection.execute(text("delete from assets"))


def v2_request() -> RegisterAssetRequest:
    return RegisterAssetRequest.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "initial_issuer_pubkey": PUBKEY,
                "name": "Issuer Action Asset",
                "precision": 8,
                "ticker": "ACTION",
                "version": 2,
            },
            "mutable": {
                "category_tags": ["stablecoin"],
                "trading_venues": [{"venue": "sideswap", "url": "https://sideswap.io/assets/ACTION"}],
                "custom": {"isin": "OLD"},
            },
        }
    )


def legacy_request() -> LegacyAssetRequest:
    return LegacyAssetRequest.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "issuer_pubkey": PUBKEY,
                "name": "Legacy Action Asset",
                "precision": 8,
                "ticker": "ACTION",
                "version": 0,
            },
        }
    )


def test_replace_and_delete_mutable_metadata(session_factory, registered_asset) -> None:
    with session_factory() as session:
        tags_response = submit_signed(session, replace_category_tags_action(["bond", "tokenized"], "nonce-tags"))
        venues_response = submit_signed(
            session,
            replace_trading_venues_action(
                [{"venue": "bitfinex", "url": "https://bitfinex.com/t/ACTION"}],
                "nonce-venues",
                timestamp=NOW + timedelta(seconds=1),
            ),
        )
        replace_custom_response = submit_signed(
            session,
            replace_custom_action({"issuer_note": "Series A"}, "nonce-replace-custom", timestamp=NOW + timedelta(seconds=2)),
        )
        custom_response = submit_signed(
            session,
            set_custom_field_action("isin", "NEW", "nonce-custom", timestamp=NOW + timedelta(seconds=3)),
        )
        delete_response = submit_signed(
            session,
            delete_custom_field_action("isin", "nonce-delete", timestamp=NOW + timedelta(seconds=4)),
        )

    assert tags_response.asset.mutable.category_tags == ["bond", "tokenized"]
    assert venues_response.asset.mutable.trading_venues[0].venue == "bitfinex"
    assert replace_custom_response.asset.mutable.custom == {"issuer_note": "Series A"}
    assert custom_response.asset.mutable.custom["isin"] == "NEW"
    assert custom_response.asset.mutable.custom["issuer_note"] == "Series A"
    assert "isin" not in delete_response.asset.mutable.custom


def test_replace_tags_and_trading_venues_normalize_case(session_factory, registered_asset) -> None:
    with session_factory() as session:
        tags_response = submit_signed(session, replace_category_tags_action(["Bond", "TOKENIZED"], "nonce-case-tags"))
        venues_response = submit_signed(
            session,
            replace_trading_venues_action(
                [{"venue": "BITFINEX", "url": "HTTPS://BITFINEX.COM/t/ACTION"}],
                "nonce-case-venues",
                timestamp=NOW + timedelta(seconds=1),
            ),
        )

    assert tags_response.asset.mutable.category_tags == ["bond", "tokenized"]
    assert tags_response.audit_entry.action["category_tags"] == ["bond", "tokenized"]
    assert venues_response.asset.mutable.trading_venues[0].venue == "bitfinex"
    assert venues_response.asset.mutable.trading_venues[0].url == "https://bitfinex.com/t/ACTION"
    assert venues_response.audit_entry.action["trading_venues"] == [
        {"venue": "bitfinex", "url": "https://bitfinex.com/t/ACTION"}
    ]


def test_deregister_action_marks_asset_inactive(session_factory, registered_asset) -> None:
    with session_factory() as session:
        response = submit_signed(session, deregister_action("nonce-deregister"))

    assert response.status == "applied"
    assert response.asset.status == "deregistered"
    assert response.audit_entry.action["operation"] == "deregister"


def test_rejects_v2_issuer_action_for_unmigrated_legacy_asset(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request())

    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            submit_signed(session, replace_custom_action({"issuer_note": "Series A"}, "nonce-unmigrated-legacy"))

    assert exc_info.value.error == "asset_not_found"
    assert "must be migrated" in exc_info.value.message


def test_nonce_idempotency_and_conflict(session_factory, registered_asset) -> None:
    action = replace_category_tags_action(["bond"], "nonce-replay")
    with session_factory() as session:
        first = submit_signed(session, action)
        retry = submit_signed(session, action)

    assert first.status == "applied"
    assert retry.status == "idempotent_retry"
    assert retry.asset is None
    assert retry.audit_entry.audit_id == first.audit_entry.audit_id

    conflicting = replace_category_tags_action(["tokenized"], "nonce-replay")
    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            submit_signed(session, conflicting)

    assert exc_info.value.error == "nonce_conflict"
    assert exc_info.value.status_code == 409


def test_latest_action_hash_endpoint_and_prev_hash_validation(session_factory, registered_asset) -> None:
    with session_factory() as session:
        initial_latest = get_latest_action_hash(session, ASSET_ID)
        response = submit_signed(session, replace_category_tags_action(["bond"], "nonce-chain"))
        advanced_latest = get_latest_action_hash(session, ASSET_ID)

    assert initial_latest.operation == "register"
    assert initial_latest.action_hash == response.audit_entry.action["prev_action_hash"]
    assert response.audit_entry.action_hash == advanced_latest.action_hash
    assert advanced_latest.operation == "replace_category_tags"

    stale_action = replace_category_tags_action(["tokenized"], "nonce-chain-stale")
    stale_action["prev_action_hash"] = initial_latest.action_hash
    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            submit_signed(session, stale_action)

    assert exc_info.value.error == "prev_action_hash_mismatch"
    assert exc_info.value.status_code == 409
    assert exc_info.value.details["expected_prev_action_hash"] == advanced_latest.action_hash
    assert exc_info.value.details["submitted_prev_action_hash"] == initial_latest.action_hash


def test_rejects_non_canonical_payload_invalid_signature_and_stale_timestamp(session_factory, registered_asset) -> None:
    action = replace_category_tags_action(["bond"], "nonce-bad")
    non_canonical_payload = b'{"signing_context":"liquid-asset-registry-action-v1", "asset_id":"' + ASSET_ID.encode() + b'"}'
    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            submit_issuer_action(session, asset_id=ASSET_ID, payload=non_canonical_payload, signature=sign_payload(action), now=NOW)
    assert exc_info.value.error == "non_canonical_payload"

    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            add_prev_action_hash(session, action)
            submit_issuer_action(session, asset_id=ASSET_ID, payload=payload_for(action), signature=sign_payload(action, NEW_PRIVATE_KEY), now=NOW)
    assert exc_info.value.error == "invalid_signature"

    stale = replace_category_tags_action(["bond"], "nonce-stale", timestamp=NOW - timedelta(minutes=6))
    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            submit_signed(session, stale)
    assert exc_info.value.error == "stale_timestamp"


def test_rejects_timestamp_older_than_last_accepted_action(session_factory, registered_asset) -> None:
    with session_factory() as session:
        submit_signed(session, replace_category_tags_action(["bond"], "nonce-newer", timestamp=NOW + timedelta(seconds=20)))

    older = replace_category_tags_action(["tokenized"], "nonce-older", timestamp=NOW + timedelta(seconds=10))
    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            submit_signed(session, older)

    assert exc_info.value.error == "stale_timestamp"


def test_rejects_issuer_no_ops_before_action_insertion(session_factory, registered_asset) -> None:
    no_ops = [
        replace_category_tags_action(["stablecoin"], "nonce-noop-tags"),
        replace_trading_venues_action(
            [{"venue": "sideswap", "url": "https://sideswap.io/assets/ACTION"}],
            "nonce-noop-venues",
            timestamp=NOW + timedelta(seconds=1),
        ),
        replace_custom_action({"isin": "OLD"}, "nonce-noop-custom", timestamp=NOW + timedelta(seconds=2)),
        set_custom_field_action("isin", "OLD", "nonce-noop-set-custom", timestamp=NOW + timedelta(seconds=3)),
        delete_custom_field_action("missing", "nonce-noop-delete", timestamp=NOW + timedelta(seconds=4)),
        {
            "signing_context": "liquid-asset-registry-action-v1",
            "asset_id": ASSET_ID,
            "operation": "rotate_issuer_pubkey",
            "timestamp": iso(NOW + timedelta(seconds=5)),
            "nonce": "nonce-noop-rotate",
            "new_issuer_pubkey": PUBKEY,
        },
    ]

    with session_factory() as session:
        for action in no_ops:
            with pytest.raises(RegistryError) as exc_info:
                submit_signed(session, action)
            assert exc_info.value.error == "no_op_action"

        inserted_no_ops = session.execute(
            text(
                """
                select count(*)
                from actions
                where nonce in (
                  'nonce-noop-tags',
                  'nonce-noop-venues',
                  'nonce-noop-custom',
                  'nonce-noop-set-custom',
                  'nonce-noop-delete',
                  'nonce-noop-rotate'
                )
                """
            )
        ).scalar_one()

    assert inserted_no_ops == 0


def test_rejects_deregistering_already_deregistered_asset_as_no_op(session_factory, registered_asset) -> None:
    with session_factory() as session:
        submit_signed(session, deregister_action("nonce-deregister"))
        with pytest.raises(RegistryError) as exc_info:
            submit_signed(session, deregister_action("nonce-noop-deregister", timestamp=NOW + timedelta(seconds=1)))
        inserted_no_op = session.execute(
            text("select count(*) from actions where nonce = 'nonce-noop-deregister'")
        ).scalar_one()

    assert exc_info.value.error == "asset_not_found"
    assert inserted_no_op == 0


def test_rotate_issuer_pubkey_closes_old_key_and_requires_new_key_for_future_actions(session_factory, registered_asset) -> None:
    rotation = {
        "signing_context": "liquid-asset-registry-action-v1",
        "asset_id": ASSET_ID,
        "operation": "rotate_issuer_pubkey",
        "timestamp": iso(NOW),
        "nonce": "nonce-rotate",
        "new_issuer_pubkey": NEW_PUBKEY,
    }

    with session_factory() as session:
        rotated = submit_signed(session, rotation)

    assert rotated.asset.current_issuer_pubkey == NEW_PUBKEY
    assert [entry.pubkey for entry in rotated.asset.issuer_pubkey_history] == [PUBKEY, NEW_PUBKEY]
    assert rotated.asset.issuer_pubkey_history[0].valid_until_audit_id == rotated.audit_entry.audit_id

    old_key_action = replace_category_tags_action(["bond"], "nonce-old-key", timestamp=NOW + timedelta(seconds=1))
    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            submit_signed(session, old_key_action, private_key=PRIVATE_KEY)
    assert exc_info.value.error == "invalid_signature"

    new_key_action = replace_category_tags_action(["bond"], "nonce-new-key", timestamp=NOW + timedelta(seconds=1))
    with session_factory() as session:
        response = submit_signed(session, new_key_action, private_key=NEW_PRIVATE_KEY)

    assert response.status == "applied"
    assert response.audit_entry.verified_pubkey == NEW_PUBKEY
    assert response.asset.mutable.category_tags == ["bond"]


def test_rejects_old_path_based_actions(session_factory, registered_asset) -> None:
    old_action = {
        "signing_context": "liquid-asset-registry-action-v1",
        "asset_id": ASSET_ID,
        "operation": "replace",
        "path": "/mutable/category_tags",
        "mutable_schema_version": 1,
        "timestamp": iso(NOW),
        "nonce": "nonce-old-path",
        "value": ["bond"],
    }

    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            submit_signed(session, old_action)

    assert exc_info.value.error == "validation_error"


def submit_signed(session, action: dict, *, private_key: int = PRIVATE_KEY):
    add_prev_action_hash(session, action)
    return submit_issuer_action(
        session,
        asset_id=ASSET_ID,
        payload=payload_for(action),
        signature=sign_payload(action, private_key),
        now=NOW,
    )


def add_prev_action_hash(session, action: dict) -> dict:
    if "prev_action_hash" not in action and action.get("operation") != "replace":
        action["prev_action_hash"] = get_latest_action_hash(session, ASSET_ID).action_hash
    return action


def replace_category_tags_action(category_tags: list[str], nonce: str, *, timestamp: datetime = NOW) -> dict:
    return {
        "signing_context": "liquid-asset-registry-action-v1",
        "asset_id": ASSET_ID,
        "operation": "replace_category_tags",
        "mutable_schema_version": 1,
        "timestamp": iso(timestamp),
        "nonce": nonce,
        "category_tags": category_tags,
    }


def replace_trading_venues_action(trading_venues: list[dict], nonce: str, *, timestamp: datetime = NOW) -> dict:
    return {
        "signing_context": "liquid-asset-registry-action-v1",
        "asset_id": ASSET_ID,
        "operation": "replace_trading_venues",
        "mutable_schema_version": 1,
        "timestamp": iso(timestamp),
        "nonce": nonce,
        "trading_venues": trading_venues,
    }


def replace_custom_action(custom: dict, nonce: str, *, timestamp: datetime = NOW) -> dict:
    return {
        "signing_context": "liquid-asset-registry-action-v1",
        "asset_id": ASSET_ID,
        "operation": "replace_custom",
        "mutable_schema_version": 1,
        "timestamp": iso(timestamp),
        "nonce": nonce,
        "custom": custom,
    }


def set_custom_field_action(custom_key: str, value, nonce: str, *, timestamp: datetime = NOW) -> dict:
    return {
        "signing_context": "liquid-asset-registry-action-v1",
        "asset_id": ASSET_ID,
        "operation": "set_custom_field",
        "mutable_schema_version": 1,
        "timestamp": iso(timestamp),
        "nonce": nonce,
        "custom_key": custom_key,
        "value": value,
    }


def delete_custom_field_action(custom_key: str, nonce: str, *, timestamp: datetime = NOW) -> dict:
    return {
        "signing_context": "liquid-asset-registry-action-v1",
        "asset_id": ASSET_ID,
        "operation": "delete_custom_field",
        "mutable_schema_version": 1,
        "timestamp": iso(timestamp),
        "nonce": nonce,
        "custom_key": custom_key,
    }


def deregister_action(nonce: str, *, timestamp: datetime = NOW) -> dict:
    return {
        "signing_context": "liquid-asset-registry-action-v1",
        "asset_id": ASSET_ID,
        "operation": "deregister",
        "timestamp": iso(timestamp),
        "nonce": nonce,
    }


def payload_for(action: dict) -> bytes:
    return canonical_json(action).encode("utf-8")


def sign_payload(action: dict, private_key: int = PRIVATE_KEY) -> str:
    message_hash = _bitcoin_signed_message_hash(payload_for(action).decode("utf-8"))
    signature = wally.ec_sig_from_bytes(
        private_key.to_bytes(wally.EC_PRIVATE_KEY_LEN, "big"),
        message_hash,
        wally.EC_FLAG_ECDSA,
    )
    return base64.b64encode(signature).decode()


def pubkey_for_private_key(private_key: int) -> str:
    return wally.ec_public_key_from_private_key(private_key.to_bytes(wally.EC_PRIVATE_KEY_LEN, "big")).hex()


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
