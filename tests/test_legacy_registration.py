import base64
import json
import os

import pytest
import wallycore as wally
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from registry_api.errors import RegistryError
from registry_api.legacy_assets import deregister_legacy_asset, get_legacy_asset, list_legacy_assets, list_legacy_assets_json_bytes
from registry_api.migration import migrate_legacy_asset_to_v2
from registry_api.registration import register_legacy_asset
from registry_api.schemas import LegacyAssetRequest, RegisterAssetRequest
from registry_api.signatures import _bitcoin_signed_message_hash
from registry_api.v2_assets import get_v2_asset, register_v2_asset


pytestmark = pytest.mark.skipif(
    not os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL"),
    reason="ASSET_REGISTRY_TEST_DATABASE_URL is required for legacy registration tests",
)

ASSET_ID = "cc909f1b00000000000000000000000000000000000000000000000000000000"
ASSET_ID_2 = "ce909f1b00000000000000000000000000000000000000000000000000000000"
V2_ASSET_ID = "cd909f1b00000000000000000000000000000000000000000000000000000000"
PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
SIGNING_PRIVATE_KEY = (1).to_bytes(wally.EC_PRIVATE_KEY_LEN, "big")
SIGNING_PUBKEY = wally.ec_public_key_from_private_key(SIGNING_PRIVATE_KEY).hex()


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


def legacy_request(asset_id: str = ASSET_ID, ticker: str | None = "LEGACY", pubkey: str = PUBKEY) -> LegacyAssetRequest:
    return LegacyAssetRequest.model_validate(
        {
            "asset_id": asset_id,
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "issuer_pubkey": pubkey,
                "name": "Legacy Asset",
                "precision": 0,
                "ticker": ticker,
                "version": 0,
            },
            "domain_verification_method": "http",
        }
    )


def legacy_request_with_contract_extras() -> LegacyAssetRequest:
    return LegacyAssetRequest.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "proof.example.com"},
                "issuer_pubkey": PUBKEY,
                "name": "Legacy Asset",
                "precision": 0,
                "ticker": "LEGACY",
                "version": 0,
                "collection": "legacy collection",
                "issuer_identifier": "issuer-123",
                "nested_extra": {"registry": "legacy"},
            },
            "domain_verification_method": "http",
        }
    )


def v2_request(asset_id: str = V2_ASSET_ID, ticker: str = "V2ASSET", pubkey: str = PUBKEY) -> RegisterAssetRequest:
    return RegisterAssetRequest.model_validate(
        {
            "asset_id": asset_id,
            "contract": {
                "entity": {"domain": "v2.example.com"},
                "initial_issuer_pubkey": pubkey,
                "name": "V2 Asset",
                "precision": 8,
                "ticker": ticker,
                "version": 2,
            },
        }
    )


def test_legacy_registration_inserts_asset_defaults_and_action(session_factory) -> None:
    with session_factory() as session:
        response = register_legacy_asset(session, legacy_request())

    assert response["asset_id"] == ASSET_ID
    assert response["ticker"] == "LEGACY"
    assert "domain_verification_method" not in response

    with session_factory() as session:
        row = session.execute(
            text(
                """
                select a.asset_id, a.contract_version, a.initial_issuer_pubkey_source,
                       a.current_issuer_pubkey, a.status, act.operation, act.action,
                       h.pubkey as history_pubkey
                from assets a
                join actions act on act.asset_uuid = a.asset_uuid
                join issuer_pubkey_history h on h.asset_uuid = a.asset_uuid
                where a.asset_id = :asset_id
                order by act.audit_sequence desc
                limit 1
                """
            ),
            {"asset_id": ASSET_ID},
        ).mappings().one()

    assert row["contract_version"] == 0
    assert row["action"]["contract_hash"]
    assert row["initial_issuer_pubkey_source"] == "registry_registration"
    assert row["current_issuer_pubkey"] == PUBKEY
    assert row["status"] == "active"
    assert row["operation"] == "legacy_register"
    assert row["history_pubkey"] == PUBKEY


def test_legacy_registration_rejects_duplicate_active_asset(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request())

    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            register_legacy_asset(session, legacy_request())

    assert exc_info.value.error == "asset_conflict"
    assert exc_info.value.status_code == 409


def test_legacy_registration_allows_multiple_untickered_assets_for_same_domain(session_factory) -> None:
    with session_factory() as session:
        first = register_legacy_asset(session, legacy_request(asset_id=ASSET_ID, ticker=None))
        second = register_legacy_asset(session, legacy_request(asset_id=ASSET_ID_2, ticker=None))

    assert "ticker" not in first
    assert "ticker" not in first["contract"]
    assert "ticker" not in second
    assert "ticker" not in second["contract"]

    with session_factory() as session:
        rows = session.execute(
            text(
                """
                select asset_id, ticker
                from assets
                where asset_id in (:first_asset_id, :second_asset_id)
                order by asset_id
                """
            ),
            {"first_asset_id": ASSET_ID, "second_asset_id": ASSET_ID_2},
        ).mappings().all()

    assert [dict(row) for row in rows] == [
        {"asset_id": ASSET_ID, "ticker": None},
        {"asset_id": ASSET_ID_2, "ticker": None},
    ]


def test_legacy_registration_allows_case_variant_tickers_for_same_domain(session_factory) -> None:
    with session_factory() as session:
        first = register_legacy_asset(session, legacy_request(asset_id=ASSET_ID, ticker="LEGACY"))
        second = register_legacy_asset(session, legacy_request(asset_id=ASSET_ID_2, ticker="legacy"))

    assert first["ticker"] == "LEGACY"
    assert second["ticker"] == "legacy"


def test_legacy_registration_rejects_failed_domain_proof_when_enforced(session_factory) -> None:
    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            register_legacy_asset(
                session,
                legacy_request(),
                enforce_domain_verification=True,
                fetch_text=lambda _url: "wrong proof",
            )

    assert exc_info.value.error == "domain_verification_failed"


def test_legacy_registration_rejects_unconfigured_chain_verifier_when_enforced(session_factory) -> None:
    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            register_legacy_asset(session, legacy_request(), enforce_chain_verification=True)

    assert exc_info.value.error == "chain_verifier_not_configured"


def test_legacy_registration_uses_configured_chain_verifier_when_enforced(session_factory) -> None:
    calls = []

    class RecordingVerifier:
        def verify_issuance_commitment(self, commitment) -> None:
            calls.append(commitment)

    with session_factory() as session:
        register_legacy_asset(session, legacy_request(), enforce_chain_verification=True, chain_verifier=RecordingVerifier())

    assert calls
    assert calls[0].asset_id == ASSET_ID


def test_legacy_lookup_and_listing_return_registered_response(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request())

    with session_factory() as session:
        asset = get_legacy_asset(session, ASSET_ID)
        listing = list_legacy_assets(session)
        listing_json = json.loads(list_legacy_assets_json_bytes(session))

    assert asset["asset_id"] == ASSET_ID
    assert asset["contract"]["ticker"] == "LEGACY"
    assert "domain_verification_method" not in asset
    assert list(listing) == [ASSET_ID]
    assert listing[ASSET_ID]["asset_id"] == ASSET_ID
    assert listing[ASSET_ID]["contract"]["ticker"] == "LEGACY"
    assert "domain_verification_method" not in listing[ASSET_ID]
    assert listing_json == listing


def test_legacy_all_json_uses_cached_serialized_fragments(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request())
        session.execute(
            text(
                """
                update asset_serialized_fragments f
                set legacy_json = :legacy_json
                from assets a
                where a.asset_uuid = f.asset_uuid and a.asset_id = :asset_id
                """
            ),
            {
                "asset_id": ASSET_ID,
                "legacy_json": json.dumps({"asset_id": ASSET_ID, "cached_fragment": True}),
            },
        )
        session.commit()

    with session_factory() as session:
        listing_json = json.loads(list_legacy_assets_json_bytes(session))

    assert listing_json[ASSET_ID]["cached_fragment"] is True


def test_legacy_contract_extras_are_stored_and_returned_after_migration(session_factory) -> None:
    with session_factory() as session:
        response = register_legacy_asset(session, legacy_request_with_contract_extras())

    assert response["contract"]["issuer_identifier"] == "issuer-123"
    assert response["contract"]["nested_extra"] == {"registry": "legacy"}

    with session_factory() as session:
        extras = session.execute(
            text("select contract_extra_fields from assets where asset_id = :asset_id"),
            {"asset_id": ASSET_ID},
        ).scalar_one()
        migrate_legacy_asset_to_v2(session, ASSET_ID)

    assert extras == {
        "collection": "legacy collection",
        "issuer_identifier": "issuer-123",
        "nested_extra": {"registry": "legacy"},
    }

    with session_factory() as session:
        legacy_asset = get_legacy_asset(session, ASSET_ID)
        v2_asset = get_v2_asset(session, ASSET_ID)

    assert legacy_asset["contract"]["issuer_identifier"] == "issuer-123"
    assert v2_asset.contract.model_extra == {
        "collection": "legacy collection",
        "issuer_identifier": "issuer-123",
        "nested_extra": {"registry": "legacy"},
    }
    assert v2_asset.contract.issuer_pubkey == PUBKEY
    assert v2_asset.contract.initial_issuer_pubkey is None


def test_legacy_fallback_response_reconstructs_contract_extras(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request_with_contract_extras())
        session.execute(text("update actions set action = '{}'::jsonb where operation = 'legacy_register'"))
        session.commit()

    with session_factory() as session:
        asset = get_legacy_asset(session, ASSET_ID)

    assert asset["contract"]["issuer_identifier"] == "issuer-123"
    assert asset["contract"]["nested_extra"] == {"registry": "legacy"}
    assert asset["contract"]["collection"] == "legacy collection"


def test_legacy_lookup_and_listing_preserve_v2_contract_shape(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())

    with session_factory() as session:
        asset = get_legacy_asset(session, V2_ASSET_ID)
        listing = list_legacy_assets(session)

    assert asset["asset_id"] == V2_ASSET_ID
    assert asset["contract"]["initial_issuer_pubkey"] == PUBKEY
    assert "issuer_pubkey" not in asset["contract"]
    assert "issuer_pubkey" not in asset
    assert listing[V2_ASSET_ID]["contract"]["initial_issuer_pubkey"] == PUBKEY
    assert "issuer_pubkey" not in listing[V2_ASSET_ID]["contract"]


def test_legacy_deregistration_marks_asset_inactive_and_inserts_action(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request(pubkey=SIGNING_PUBKEY))

    signature = deletion_signature(ASSET_ID)
    with session_factory() as session:
        message = deregister_legacy_asset(session, ASSET_ID, signature)

    assert message == "Asset deleted"
    with session_factory() as session:
        row = session.execute(
            text(
                """
                select a.status, act.operation, act.verified_pubkey
                from assets a
                join actions act on act.asset_uuid = a.asset_uuid
                where a.asset_id = :asset_id
                order by act.audit_sequence desc
                limit 1
                """
            ),
            {"asset_id": ASSET_ID},
        ).mappings().one()
        fragment_count = session.execute(
            text(
                """
                select count(*)
                from asset_serialized_fragments f
                join assets a on a.asset_uuid = f.asset_uuid
                where a.asset_id = :asset_id
                """
            ),
            {"asset_id": ASSET_ID},
        ).scalar_one()

    assert row["status"] == "deregistered"
    assert row["operation"] == "legacy_deregister"
    assert row["verified_pubkey"] == SIGNING_PUBKEY
    assert fragment_count == 0


def test_legacy_lookup_and_listing_exclude_deregistered_assets(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request(pubkey=SIGNING_PUBKEY))

    with session_factory() as session:
        deregister_legacy_asset(session, ASSET_ID, deletion_signature(ASSET_ID))

    with session_factory() as session:
        listing = list_legacy_assets(session)
        listing_json = json.loads(list_legacy_assets_json_bytes(session))
        with pytest.raises(RegistryError) as exc_info:
            get_legacy_asset(session, ASSET_ID)

    assert listing == {}
    assert listing_json == {}
    assert exc_info.value.error == "asset_not_found"
    assert exc_info.value.status_code == 404


def test_legacy_allows_reregistration_after_deregistration(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request(pubkey=SIGNING_PUBKEY))

    with session_factory() as session:
        deregister_legacy_asset(session, ASSET_ID, deletion_signature(ASSET_ID))

    with session_factory() as session:
        response = register_legacy_asset(session, legacy_request(pubkey=SIGNING_PUBKEY, ticker="LEGACY2"))

    assert response["asset_id"] == ASSET_ID
    assert response["contract"]["ticker"] == "LEGACY2"

    with session_factory() as session:
        asset = get_legacy_asset(session, ASSET_ID)
        listing = list_legacy_assets(session)
        listing_json = json.loads(list_legacy_assets_json_bytes(session))
        rows = session.execute(
            text("select status from assets where asset_id = :asset_id order by created_at"),
            {"asset_id": ASSET_ID},
        ).scalars().all()

    assert asset["contract"]["ticker"] == "LEGACY2"
    assert list(listing) == [ASSET_ID]
    assert list(listing_json) == [ASSET_ID]
    assert rows == ["deregistered", "active"]


def test_legacy_deregistration_rejects_invalid_signature(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request(pubkey=SIGNING_PUBKEY))

    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            deregister_legacy_asset(session, ASSET_ID, base64.b64encode(b"0" * 64).decode())

    assert exc_info.value.error == "invalid_signature"


def test_v1_to_v2_migration_marks_source_and_is_idempotent(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request())

    with session_factory() as session:
        response = migrate_legacy_asset_to_v2(session, ASSET_ID)

    assert response.status == "applied"
    assert response.audit_entry.action["operation"] == "migrate_contract_metadata"

    with session_factory() as session:
        retry = migrate_legacy_asset_to_v2(session, ASSET_ID)
        source = session.execute(
            text("select initial_issuer_pubkey_source from assets where asset_id = :asset_id"),
            {"asset_id": ASSET_ID},
        ).scalar_one()

    assert retry.status == "idempotent_retry"
    assert source == "migrated_legacy_record"


def test_v1_to_v2_migration_rejects_untickered_legacy_asset(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request(ticker=None))

    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            migrate_legacy_asset_to_v2(session, ASSET_ID)

    assert exc_info.value.error == "validation_error"
    assert exc_info.value.message == "legacy assets without a ticker cannot be migrated to v2"


def deletion_signature(asset_id: str) -> str:
    message_hash = _bitcoin_signed_message_hash(f"remove {asset_id} from registry")
    signature = wally.ec_sig_from_bytes(SIGNING_PRIVATE_KEY, message_hash, wally.EC_FLAG_ECDSA)
    return base64.b64encode(signature).decode()
