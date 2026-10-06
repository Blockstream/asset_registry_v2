import asyncio
import base64
import json
import os
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from threading import Event
from time import monotonic, sleep

import pytest
import wallycore as wally
from sqlalchemy import create_engine, delete, event, text
from sqlalchemy.orm import sessionmaker

from registry_api.canonical_json import canonical_json_bytes, contract_hash
from registry_api.domain_verification import (
    expected_http_proof,
    expected_pubkey_bound_domain_proof,
    normalized_contract_signature_payload,
)
from registry_api.errors import RegistryError
from registry_api.models import Action, Asset, AssetCategoryTag
from registry_api.schemas import RegisterAssetRequest
from registry_api.serialized_fragments import refresh_asset_serialized_fragments
from registry_api.signatures import _bitcoin_signed_message_hash
from registry_api.v2_assets import (
    all_v2_assets_json_bytes,
    get_v2_asset,
    register_v2_asset,
    search_v2_assets,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL"),
    reason="ASSET_REGISTRY_TEST_DATABASE_URL is required for v2 asset tests",
)

ASSET_ID = "dd909f1b00000000000000000000000000000000000000000000000000000000"
ASSET_ID_2 = "ee909f1b00000000000000000000000000000000000000000000000000000000"
PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
PUBKEY_2 = "0282375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
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


def v2_request(
    *,
    asset_id: str = ASSET_ID,
    pubkey: str = PUBKEY,
    domain: str = "proof.example.com",
    ticker: str = "V2ASSET",
    name: str = "V2 Asset",
    categories: list[str] | None = None,
    venues: list[dict[str, str]] | None = None,
    domain_verification_method: str = "http",
) -> RegisterAssetRequest:
    return RegisterAssetRequest.model_validate(
        {
            "asset_id": asset_id,
            "contract": {
                "entity": {"domain": domain},
                "initial_issuer_pubkey": pubkey,
                "name": name,
                "precision": 8,
                "ticker": ticker,
                "version": 2,
            },
            "domain_verification_method": domain_verification_method,
            "mutable": {
                "category_tags": categories or ["stablecoin"],
                "trading_venues": venues or [{"venue": "sideswap", "url": "https://sideswap.io/assets/V2ASSET"}],
                "custom": {"issuer_reference": "abc-123", "rank": 7},
            },
        }
    )


def test_v2_registration_persists_normalized_metadata_and_returns_asset(session_factory) -> None:
    with session_factory() as session:
        response = register_v2_asset(session, v2_request())

    assert response.asset_id == ASSET_ID
    assert response.contract.initial_issuer_pubkey == PUBKEY
    assert response.initial_issuer_pubkey_source == "contract"
    assert response.mutable.category_tags == ["stablecoin"]
    assert response.mutable.trading_venues[0].venue == "sideswap"
    assert response.mutable.custom == {"issuer_reference": "abc-123", "rank": 7}
    assert response.icon is None
    assert response.issuer_pubkey_history[0].valid_from_audit_id >= 1

    with session_factory() as session:
        row = session.execute(
            text(
                """
                select a.asset_id, act.operation, tv.name as venue, ct.tag
                from assets a
                join actions act on act.asset_uuid = a.asset_uuid
                join asset_trading_venues tv on tv.asset_uuid = a.asset_uuid
                join asset_category_tags ct on ct.asset_uuid = a.asset_uuid
                where a.asset_id = :asset_id
                """
            ),
            {"asset_id": ASSET_ID},
        ).mappings().one()

    assert row["operation"] == "register"
    assert row["venue"] == "sideswap"
    assert row["tag"] == "stablecoin"


def test_v2_registration_preserves_contract_hash_in_all_responses(session_factory) -> None:
    request = v2_request(pubkey=PUBKEY.upper())
    expected_contract = request.contract.model_dump(mode="json")
    expected_hash = contract_hash(expected_contract)

    with session_factory() as session:
        registered = register_v2_asset(session, request)

    with session_factory() as session:
        asset = session.query(Asset).filter_by(asset_id=ASSET_ID).one()
        action = session.query(Action).filter_by(asset_uuid=asset.asset_uuid).one()
        assert action.action["contract"] == expected_contract
        assert action.action["contract_hash"] == expected_hash
        found = get_v2_asset(session, ASSET_ID)
        listed = search_v2_assets(session).items[0]
        all_json = json.loads(all_v2_assets_json_bytes(session))[ASSET_ID]

    for payload in (
        registered.model_dump(mode="json"),
        found.model_dump(mode="json"),
        listed.model_dump(mode="json"),
        all_json,
    ):
        assert payload["contract"] == expected_contract
        assert contract_hash(payload["contract"]) == expected_hash


def test_v2_all_json_uses_cached_serialized_fragments(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())
        session.execute(
            text(
                """
                update asset_serialized_fragments f
                set v2_json = :v2_json
                from assets a
                where a.asset_uuid = f.asset_uuid and a.asset_id = :asset_id
                """
            ),
            {
                "asset_id": ASSET_ID,
                "v2_json": json.dumps({"asset_id": ASSET_ID, "cached_fragment": True}),
            },
        )
        session.commit()

    with session_factory() as session:
        payload = json.loads(all_v2_assets_json_bytes(session))

    assert payload[ASSET_ID]["cached_fragment"] is True


def test_refresh_serialized_fragments_expires_loaded_relationships(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request(categories=["stablecoin"]))

    with session_factory() as session:
        asset = session.query(Asset).filter_by(asset_id=ASSET_ID).one()
        assert [row.tag for row in asset.category_tags] == ["stablecoin"]

        asset.updated_at = datetime.now(UTC)
        session.execute(delete(AssetCategoryTag).where(AssetCategoryTag.asset_uuid == asset.asset_uuid))
        session.add(AssetCategoryTag(asset_uuid=asset.asset_uuid, tag="bond", position=0))
        session.flush()
        refresh_asset_serialized_fragments(session, asset)
        session.commit()

    with session_factory() as session:
        payload = json.loads(all_v2_assets_json_bytes(session))

    assert payload[ASSET_ID]["mutable"]["category_tags"] == ["bond"]


def test_v2_registration_rejects_duplicate_active_asset(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())

    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            register_v2_asset(session, v2_request(ticker="OTHER"))

    assert exc_info.value.error == "asset_conflict"
    assert exc_info.value.status_code == 409


def test_v2_registration_allows_case_variant_tickers_for_same_domain(session_factory) -> None:
    with session_factory() as session:
        first = register_v2_asset(session, v2_request(asset_id=ASSET_ID, ticker="V2ASSET"))
        second = register_v2_asset(session, v2_request(asset_id=ASSET_ID_2, ticker="v2asset"))

    assert first.contract.ticker == "V2ASSET"
    assert second.contract.ticker == "v2asset"


def test_v2_registration_keeps_existing_asset_bound_http_domain_proof(session_factory) -> None:
    request = v2_request()

    with session_factory() as session:
        response = register_v2_asset(
            session,
            request,
            enforce_domain_verification=True,
            fetch_text=lambda _url: expected_http_proof("proof.example.com", ASSET_ID),
        )

    assert response.asset_id == ASSET_ID


def test_v2_registration_accepts_pubkey_bound_http_domain_proof(session_factory) -> None:
    request = v2_request(pubkey=SIGNING_PUBKEY)

    with session_factory() as session:
        response = register_v2_asset(
            session,
            request,
            enforce_domain_verification=True,
            fetch_text=lambda _url: expected_pubkey_bound_domain_proof(SIGNING_PUBKEY),
            domain_signature=signed_message(normalized_contract_signature_payload(request.contract)),
        )

    assert response.asset_id == ASSET_ID
    assert response.initial_issuer_pubkey == SIGNING_PUBKEY


def test_v2_registration_accepts_pubkey_bound_dns_domain_proof(session_factory) -> None:
    request = v2_request(pubkey=SIGNING_PUBKEY, domain_verification_method="dns")

    with session_factory() as session:
        response = register_v2_asset(
            session,
            request,
            enforce_domain_verification=True,
            resolve_txt=lambda _domain: ["unrelated", expected_pubkey_bound_domain_proof(SIGNING_PUBKEY)],
            domain_signature=signed_message(normalized_contract_signature_payload(request.contract)),
        )

    assert response.asset_id == ASSET_ID


def test_v2_pubkey_bound_domain_proof_requires_signature(session_factory) -> None:
    request = v2_request(pubkey=SIGNING_PUBKEY)

    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            register_v2_asset(
                session,
                request,
                enforce_domain_verification=True,
                fetch_text=lambda _url: expected_pubkey_bound_domain_proof(SIGNING_PUBKEY),
            )

    assert exc_info.value.error == "invalid_signature"
    assert exc_info.value.status_code == 401


def test_v2_pubkey_bound_domain_proof_rejects_mismatched_initial_issuer(session_factory) -> None:
    request = v2_request(pubkey=PUBKEY)

    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            register_v2_asset(
                session,
                request,
                enforce_domain_verification=True,
                fetch_text=lambda _url: expected_pubkey_bound_domain_proof(SIGNING_PUBKEY),
                domain_signature=signed_message(normalized_contract_signature_payload(request.contract)),
            )

    assert exc_info.value.error == "domain_verification_failed"
    assert exc_info.value.details == {"proof_pubkey": SIGNING_PUBKEY, "initial_issuer_pubkey": PUBKEY}


def test_v2_pubkey_bound_domain_proof_reports_request_body_signature(session_factory) -> None:
    request = v2_request(pubkey=SIGNING_PUBKEY)

    with pytest.raises(RegistryError) as exc_info:
        with session_factory() as session:
            register_v2_asset(
                session,
                request,
                enforce_domain_verification=True,
                fetch_text=lambda _url: expected_pubkey_bound_domain_proof(SIGNING_PUBKEY),
                domain_signature=signed_message(canonical_json_bytes(request.model_dump(mode="json", exclude_none=True))),
                registration_payload=canonical_json_bytes(request.model_dump(mode="json", exclude_none=True)),
            )

    assert exc_info.value.error == "invalid_signature"
    assert exc_info.value.message == (
        "domain proof signature must cover the normalized contract JSON, not the registration request body"
    )
    assert exc_info.value.details == {"expected_payload": "normalized_contract"}


def test_v2_lookup_search_filters_pagination_and_all_json(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())
    with session_factory() as session:
        register_v2_asset(
            session,
            v2_request(
                asset_id=ASSET_ID_2,
                pubkey=PUBKEY_2,
                domain="issuer.example.com",
                ticker="BONDY",
                name="Bond Asset",
                categories=["bond", "tokenized"],
                venues=[{"venue": "bitfinex", "url": "https://bitfinex.com/t/BONDY"}],
            ),
        )
    with session_factory() as session:
        asset = session.query(Asset).filter_by(asset_id=ASSET_ID).one()
        action = session.query(Action).filter_by(asset_uuid=asset.asset_uuid).one()
        asset.admin_annotations.last_admin_action_uuid = action.action_uuid
        session.flush()
        refresh_asset_serialized_fragments(session, asset)
        session.commit()

    with session_factory() as session:
        found = get_v2_asset(session, ASSET_ID)
        second = get_v2_asset(session, ASSET_ID_2)
        asset_id_filtered = search_v2_assets(session, asset_id=ASSET_ID_2[:12])
        filtered = search_v2_assets(session, category_tag=["bond"], trading_venue="bitfinex")
        first_page = search_v2_assets(session, page=1, page_size=1, sort="ticker_asc")
        all_assets_json = json.loads(all_v2_assets_json_bytes(session))

    assert found.contract.ticker == "V2ASSET"
    assert asset_id_filtered.total_count == 1
    assert asset_id_filtered.items[0].asset_id == ASSET_ID_2
    assert filtered.total_count == 1
    assert filtered.items[0].asset_id == ASSET_ID_2
    assert first_page.total_count == 2
    assert first_page.total_pages == 2
    assert first_page.items[0].contract.ticker == "BONDY"
    assert list(all_assets_json) == [ASSET_ID, ASSET_ID_2]
    assert all_assets_json == {
        ASSET_ID: found.model_dump(mode="json"),
        ASSET_ID_2: second.model_dump(mode="json"),
    }


def test_v2_search_sorts_text_fields_in_both_directions(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())
    with session_factory() as session:
        register_v2_asset(
            session,
            v2_request(
                asset_id=ASSET_ID_2,
                pubkey=PUBKEY_2,
                domain="issuer.example.com",
                ticker="BONDY",
                name="Bond Asset",
            ),
        )

    with session_factory() as session:
        sorted_asset_ids = {
            sort: [item.asset_id for item in search_v2_assets(session, sort=sort).items]
            for sort in (
                "domain_asc",
                "domain_desc",
                "name_asc",
                "name_desc",
                "ticker_asc",
                "ticker_desc",
            )
        }

    assert sorted_asset_ids == {
        "domain_asc": [ASSET_ID_2, ASSET_ID],
        "domain_desc": [ASSET_ID, ASSET_ID_2],
        "name_asc": [ASSET_ID_2, ASSET_ID],
        "name_desc": [ASSET_ID, ASSET_ID_2],
        "ticker_asc": [ASSET_ID_2, ASSET_ID],
        "ticker_desc": [ASSET_ID, ASSET_ID_2],
    }


def test_v2_search_sorts_ids_and_timestamps_in_both_directions(
    session_factory,
) -> None:
    older_created_at = datetime(2026, 1, 1, tzinfo=UTC)
    newer_created_at = datetime(2026, 1, 2, tzinfo=UTC)
    older_updated_at = datetime(2026, 1, 3, tzinfo=UTC)
    newer_updated_at = datetime(2026, 1, 4, tzinfo=UTC)

    with session_factory() as session:
        register_v2_asset(session, v2_request())
        register_v2_asset(
            session,
            v2_request(
                asset_id=ASSET_ID_2,
                pubkey=PUBKEY_2,
                domain="issuer.example.com",
                ticker="BONDY",
            ),
        )
        session.execute(
            text(
                "update assets set created_at = :created_at, updated_at = :updated_at "
                "where asset_id = :asset_id"
            ),
            {
                "asset_id": ASSET_ID,
                "created_at": older_created_at,
                "updated_at": newer_updated_at,
            },
        )
        session.execute(
            text(
                "update assets set created_at = :created_at, updated_at = :updated_at "
                "where asset_id = :asset_id"
            ),
            {
                "asset_id": ASSET_ID_2,
                "created_at": newer_created_at,
                "updated_at": older_updated_at,
            },
        )
        session.commit()

    with session_factory() as session:
        sorted_asset_ids = {
            sort: [item.asset_id for item in search_v2_assets(session, sort=sort).items]
            for sort in (
                "asset_id_asc",
                "asset_id_desc",
                "created_at_asc",
                "created_at_desc",
                "updated_at_asc",
                "updated_at_desc",
            )
        }

    assert sorted_asset_ids == {
        "asset_id_asc": [ASSET_ID, ASSET_ID_2],
        "asset_id_desc": [ASSET_ID_2, ASSET_ID],
        "created_at_asc": [ASSET_ID, ASSET_ID_2],
        "created_at_desc": [ASSET_ID_2, ASSET_ID],
        "updated_at_asc": [ASSET_ID_2, ASSET_ID],
        "updated_at_desc": [ASSET_ID, ASSET_ID_2],
    }


def test_v2_search_filters_are_case_insensitive(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(
            session,
            v2_request(ticker="SAT", name="Satoshi Asset"),
        )
        asset = session.query(Asset).filter_by(asset_id=ASSET_ID).one()
        asset.admin_annotations.asset_type = "AMP_asset"
        session.commit()

    with session_factory() as session:
        results = [
            search_v2_assets(session, asset_id=ASSET_ID.upper()[:12]),
            search_v2_assets(session, domain="PROOF.EXAMPLE.COM"),
            search_v2_assets(session, ticker="sAt"),
            search_v2_assets(session, name="satoshi"),
            search_v2_assets(session, name="SaToShI"),
            search_v2_assets(session, asset_type="amp_ASSET"),
            search_v2_assets(session, category_tag=["STABLECOIN"]),
            search_v2_assets(session, trading_venue="SIDESWAP"),
        ]

    for result in results:
        assert result.total_count == 1
        assert [item.asset_id for item in result.items] == [ASSET_ID]


def test_v2_search_filters_created_and_updated_after_strictly(session_factory) -> None:
    older_created_at = datetime(2026, 1, 1, tzinfo=UTC)
    newer_created_at = datetime(2026, 1, 15, tzinfo=UTC)
    older_updated_at = datetime(2026, 1, 20, tzinfo=UTC)
    created_cutoff = datetime(2026, 1, 10, tzinfo=UTC)

    with session_factory() as session:
        register_v2_asset(session, v2_request())
        register_v2_asset(
            session,
            v2_request(
                asset_id=ASSET_ID_2,
                pubkey=PUBKEY_2,
                domain="issuer.example.com",
                ticker="NEWER",
            ),
        )
        session.execute(
            text("update assets set created_at = :created_at, updated_at = :updated_at where asset_id = :asset_id"),
            {"asset_id": ASSET_ID, "created_at": older_created_at, "updated_at": older_updated_at},
        )
        session.execute(
            text("update assets set created_at = :created_at, updated_at = :updated_at where asset_id = :asset_id"),
            {"asset_id": ASSET_ID_2, "created_at": newer_created_at, "updated_at": newer_created_at},
        )
        session.commit()

    with session_factory() as session:
        created = search_v2_assets(session, created_after=created_cutoff)
        updated = search_v2_assets(session, updated_after=created_cutoff)
        both = search_v2_assets(session, created_after=created_cutoff, updated_after=created_cutoff)
        created_at_boundary = search_v2_assets(session, created_after=newer_created_at)
        updated_at_boundary = search_v2_assets(session, updated_after=older_updated_at)

    assert [item.asset_id for item in created.items] == [ASSET_ID_2]
    assert created.total_count == 1
    assert {item.asset_id for item in updated.items} == {ASSET_ID, ASSET_ID_2}
    assert updated.total_count == 2
    assert [item.asset_id for item in both.items] == [ASSET_ID_2]
    assert created_at_boundary.items == []
    assert updated_at_boundary.items == []


def test_v2_search_escapes_like_wildcards(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())

    with session_factory() as session:
        percent_name = search_v2_assets(session, name="%")
        underscore_name = search_v2_assets(session, name="_")
        percent_ticker = search_v2_assets(session, ticker="%")
        normal_prefix = search_v2_assets(session, name="V2")

    assert percent_name.items == []
    assert underscore_name.items == []
    assert percent_ticker.items == []
    assert normal_prefix.total_count == 1


def test_v2_search_excludes_deregistered_by_default(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())

    with session_factory() as session:
        asset = session.query(Asset).filter_by(asset_id=ASSET_ID).one()
        asset.status = "deregistered"
        session.commit()

    with session_factory() as session:
        default = search_v2_assets(session)
        including = search_v2_assets(session, include_deregistered=True)

    assert default.items == []
    assert [item.status for item in including.items] == ["deregistered"]


def test_v2_lookup_list_and_all_json_exclude_deregistered_by_default(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())

    with session_factory() as session:
        asset = session.query(Asset).filter_by(asset_id=ASSET_ID).one()
        asset.status = "deregistered"
        session.commit()

    with session_factory() as session:
        listing = search_v2_assets(session)
        all_assets_json = json.loads(all_v2_assets_json_bytes(session))
        with pytest.raises(RegistryError) as exc_info:
            get_v2_asset(session, ASSET_ID)

    assert listing.items == []
    assert all_assets_json == {}
    assert exc_info.value.error == "asset_not_found"
    assert exc_info.value.status_code == 404


def test_v2_allows_reregistration_after_deregistration(session_factory) -> None:
    with session_factory() as session:
        register_v2_asset(session, v2_request())

    with session_factory() as session:
        asset = session.query(Asset).filter_by(asset_id=ASSET_ID).one()
        asset.status = "deregistered"
        session.commit()

    with session_factory() as session:
        response = register_v2_asset(session, v2_request(name="V2 Asset Reregistered"))

    assert response.asset_id == ASSET_ID
    assert response.contract.name == "V2 Asset Reregistered"

    with session_factory() as session:
        asset = get_v2_asset(session, ASSET_ID)
        listing = search_v2_assets(session)
        all_assets_json = json.loads(all_v2_assets_json_bytes(session))
        rows = session.execute(
            text("select status from assets where asset_id = :asset_id order by created_at"),
            {"asset_id": ASSET_ID},
        ).scalars().all()

    assert asset.contract.name == "V2 Asset Reregistered"
    assert listing.total_count == 1
    assert list(all_assets_json) == [ASSET_ID]
    assert rows == ["deregistered", "active"]


def signed_message(payload: bytes) -> str:
    message_hash = _bitcoin_signed_message_hash(payload.decode("utf-8"))
    signature = wally.ec_sig_from_bytes(SIGNING_PRIVATE_KEY, message_hash, wally.EC_FLAG_ECDSA)
    return base64.b64encode(signature).decode()


@pytest.fixture()
def asset_cache_client(session_factory, monkeypatch):
    from fastapi.testclient import TestClient

    from registry_api import serialized_fragments
    from registry_api.db import get_db
    from registry_api.main import create_app

    app = create_app()

    def test_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = test_db
    monkeypatch.setattr(serialized_fragments, "SessionLocal", session_factory)
    with TestClient(app) as client:
        yield client


def backdate_registry_version(session_factory) -> None:
    # Move the version out of the current second so validators can be reused.
    with session_factory() as session:
        session.execute(text("update asset_registry_version set modified_at = '2020-01-01T00:00:00Z'"))
        session.commit()


def test_asset_cache_invalidates_after_issuer_metadata_change(session_factory, asset_cache_client):
    from registry_api.issuer_actions import get_latest_action_hash, submit_issuer_action

    paths = ["/", "/index.json", f"/{ASSET_ID}", f"/{ASSET_ID.upper()}", "/v2/assets", "/v2/assets/all.json", f"/v2/assets/{ASSET_ID}", f"/v2/assets/{ASSET_ID.upper()}"]
    with session_factory() as session:
        register_v2_asset(session, v2_request(pubkey=SIGNING_PUBKEY))
        session.commit()
    backdate_registry_version(session_factory)

    dates = {path: asset_cache_client.get(path).headers["Last-Modified"] for path in paths}
    for path in paths:
        assert asset_cache_client.get(path, headers={"If-Modified-Since": dates[path]}).status_code == 304

    with session_factory() as session:
        action = {
            "signing_context": "liquid-asset-registry-action-v1",
            "asset_id": ASSET_ID,
            "operation": "set_custom_field",
            "nonce": "cache-metadata-change",
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "prev_action_hash": get_latest_action_hash(session, ASSET_ID).action_hash,
            "mutable_schema_version": 1,
            "custom_key": "cache_test",
            "value": "updated",
        }
        payload = canonical_json_bytes(action)
        submit_issuer_action(session, asset_id=ASSET_ID, payload=payload, signature=signed_message(payload))

    for path in paths:
        response = asset_cache_client.get(path, headers={"If-Modified-Since": dates[path]})
        assert response.status_code == 200
        assert response.headers["Last-Modified"] != dates[path]
    assert asset_cache_client.get(f"/v2/assets/{ASSET_ID}").json()["mutable"]["custom"]["cache_test"] == "updated"


def test_asset_cache_invalidates_filtered_and_empty_lists_after_deregistration(session_factory, asset_cache_client):
    from registry_api.legacy_assets import deregister_legacy_asset

    paths = ["/", "/index.json", "/v2/assets/all.json", "/v2/assets?ticker=V2ASSET", "/v2/assets?page=2&page_size=1", "/v2/assets?name=NoMatch"]
    with session_factory() as session:
        register_v2_asset(session, v2_request(pubkey=SIGNING_PUBKEY))
        session.commit()
    backdate_registry_version(session_factory)

    dates = {path: asset_cache_client.get(path).headers["Last-Modified"] for path in paths}
    with session_factory() as session:
        deregister_legacy_asset(session, ASSET_ID, signed_message(f"remove {ASSET_ID} from registry".encode()))

    for path in paths:
        response = asset_cache_client.get(path, headers={"If-Modified-Since": dates[path]})
        assert response.status_code == 200
        assert response.headers["Last-Modified"] != dates[path]
        if path.startswith("/v2/assets?"):
            assert response.json()["items"] == []
        else:
            assert response.json() == {}
    for path in [f"/{ASSET_ID}", f"/v2/assets/{ASSET_ID}"]:
        assert asset_cache_client.get(path, headers={"If-Modified-Since": dates["/"]}).status_code == 404
    backdate_registry_version(session_factory)
    for path in paths:
        date = asset_cache_client.get(path).headers["Last-Modified"]
        assert asset_cache_client.get(path, headers={"If-Modified-Since": date}).status_code == 304


def test_asset_read_does_not_validate_against_in_flight_version_bump(session_factory, asset_cache_client):
    with session_factory() as session:
        register_v2_asset(session, v2_request())
        visible_version = session.scalar(
            text(
                "update asset_registry_version "
                "set modified_at = date_trunc('second', clock_timestamp()) + interval '1 second' "
                "returning modified_at"
            )
        )
        session.commit()
    path = f"/v2/assets/{ASSET_ID}"

    with session_factory() as writer:
        writer.execute(text("update assets set name = 'Pending' where asset_id = :asset_id"), {"asset_id": ASSET_ID})
        writer.execute(text("set constraints all immediate"))
        wait_for_database_second_after(writer, visible_version + timedelta(seconds=1))

        started = monotonic()
        snapshot = asset_cache_client.get(path)
        assert monotonic() - started < 2
        date = format_datetime(visible_version.astimezone(UTC), usegmt=True)
        assert snapshot.headers["Last-Modified"] == date
        assert snapshot.json()["contract"]["name"] == "V2 Asset"
        writer.commit()

    response = asset_cache_client.get(path, headers={"If-Modified-Since": date})
    assert response.status_code == 200
    assert response.json()["contract"]["name"] == "Pending"


def test_registry_version_follows_commit_order(session_factory, asset_cache_client):
    with session_factory() as session:
        register_v2_asset(session, v2_request(pubkey=SIGNING_PUBKEY))
        session.commit()
    backdate_registry_version(session_factory)

    commit_slow = Event()
    slow_ready: Future[int] = Future()

    def slow_writer():
        try:
            with session_factory() as slow:
                slow.execute(text("set local lock_timeout = '10s'"))
                slow.execute(
                    text("update assets set name = 'Slow' where asset_id = :asset_id"),
                    {"asset_id": ASSET_ID},
                )
                slow_ready.set_result(slow.scalar(text("select pg_backend_pid()")))
                assert commit_slow.wait(timeout=10), "Slow writer was not released"
                slow.commit()
        except BaseException as exc:
            if not slow_ready.done():
                slow_ready.set_exception(exc)
            raise

    path = f"/v2/assets/{ASSET_ID}"
    with ThreadPoolExecutor(max_workers=1) as pool:
        slow_commit = pool.submit(slow_writer)
        try:
            slow_pid = slow_ready.result(timeout=10)
            with session_factory() as fast:
                fast.execute(text("update asset_admin_annotations set featured = not featured"))
                fast.commit()
                fast_version = fast.scalar(text("select modified_at from asset_registry_version"))
                wait_for_database_second_after(fast, fast_version)

            # Use a stable validator so the current-second safeguard cannot
            # hide a missing version bump when the slow writer commits.
            fast_date = format_datetime(fast_version.astimezone(UTC), usegmt=True)
            snapshot = asset_cache_client.get(path)
            assert snapshot.headers["Last-Modified"] == fast_date
            assert snapshot.json()["contract"]["name"] == "V2 Asset"
            assert asset_cache_client.get(path, headers={"If-Modified-Since": fast_date}).status_code == 304

            with session_factory() as reader, session_factory() as observer:
                assert reader.scalar(text("select modified_at from asset_registry_version for share")) == fast_version
                reader_pid = reader.scalar(text("select pg_backend_pid()"))
                commit_slow.set()

                deadline = monotonic() + 5
                while not observer.scalar(
                    text("select :reader_pid = any(pg_blocking_pids(:slow_pid))"),
                    {"reader_pid": reader_pid, "slow_pid": slow_pid},
                ):
                    if slow_commit.done():
                        slow_commit.result()
                        pytest.fail("Slow writer committed without taking the version lock")
                    assert monotonic() < deadline, "Slow writer did not wait for the version lock"
                    sleep(0.01)

                # Cross another database second while the writer is blocked.
                # Its timestamp must be assigned after it acquires the lock.
                blocked_second = observer.scalar(text("select date_trunc('second', clock_timestamp())"))
                wait_for_database_second_after(observer, blocked_second)
                release_second = observer.scalar(text("select date_trunc('second', clock_timestamp())"))
                assert not slow_commit.done()

            slow_commit.result(timeout=10)
        finally:
            commit_slow.set()

    with session_factory() as session:
        slow_version = session.scalar(text("select modified_at from asset_registry_version"))
        assert slow_version > fast_version
        assert slow_version >= release_second
        wait_for_database_second_after(session, slow_version)

    response = asset_cache_client.get(path, headers={"If-Modified-Since": fast_date})
    assert response.status_code == 200
    assert response.json()["contract"]["name"] == "Slow"
    slow_date = format_datetime(slow_version.astimezone(UTC), usegmt=True)
    assert response.headers["Last-Modified"] == slow_date
    assert asset_cache_client.get(path, headers={"If-Modified-Since": slow_date}).status_code == 304


def wait_for_database_second_after(session, timestamp):
    session.execute(
        text(
            "select pg_sleep(greatest(0, extract(epoch from "
            "(cast(:timestamp as timestamptz) + interval '1 second' - clock_timestamp()))))"
        ),
        {"timestamp": timestamp},
    )


def test_issuer_commit_waiting_for_asset_read_does_not_block_event_loop(
    session_factory, asset_cache_client, monkeypatch
):
    import httpx

    from registry_api.api import v2
    from registry_api.db import get_db
    from registry_api.issuer_actions import get_latest_action_hash

    with session_factory() as session:
        register_v2_asset(session, v2_request(pubkey=SIGNING_PUBKEY))
        prev_action_hash = get_latest_action_hash(session, ASSET_ID).action_hash

    reader_started = Event()
    release_reader = Event()
    commit_started = Event()
    original_search = v2.search_v2_assets

    def slow_search(db, **filters):
        # Hold FOR SHARE until teardown, as prepare_asset_read once did.
        db.execute(text("select 1 from asset_registry_version for share"))
        reader_started.set()
        assert release_reader.wait(timeout=10), "Reader was not released"
        return original_search(db, **filters)

    def test_db():
        with session_factory() as session:
            # Bound the old event-loop deadlock so the regression test can fail.
            session.execute(text("set local lock_timeout = '5s'"))
            yield session

    def before_commit(_session):
        if reader_started.is_set():
            commit_started.set()

    action = {
        "signing_context": "liquid-asset-registry-action-v1",
        "asset_id": ASSET_ID,
        "operation": "set_custom_field",
        "nonce": "async-commit-regression",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "prev_action_hash": prev_action_hash,
        "mutable_schema_version": 1,
        "custom_key": "async_test",
        "value": "committed",
    }
    payload = canonical_json_bytes(action)
    monkeypatch.setattr(v2, "search_v2_assets", slow_search)
    asset_cache_client.app.dependency_overrides[get_db] = test_db

    async def check_requests():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=asset_cache_client.app),
            base_url="http://testserver",
        ) as client:
            reader = asyncio.create_task(client.get("/v2/assets"))
            tasks = [reader]
            try:
                assert await asyncio.to_thread(reader_started.wait, 5)
                writer = asyncio.create_task(
                    client.post(
                        f"/v2/assets/{ASSET_ID}/actions",
                        content=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Asset-Registry-Signature": signed_message(payload),
                        },
                    )
                )
                tasks.append(writer)
                assert await asyncio.to_thread(commit_started.wait, 5)
                health = await asyncio.wait_for(client.get("/health"), timeout=2)
                assert health.status_code == 200
                assert not writer.done(), "Commit should still be waiting for FOR SHARE"
                release_reader.set()
                read_response, write_response = await asyncio.wait_for(
                    asyncio.gather(reader, writer), timeout=10
                )
                assert read_response.status_code == 200
                assert write_response.status_code == 200, write_response.text
                assert write_response.json()["asset"]["mutable"]["custom"]["async_test"] == "committed"
            finally:
                release_reader.set()
                await asyncio.gather(*tasks, return_exceptions=True)

    event.listen(session_factory.class_, "before_commit", before_commit)
    try:
        asyncio.run(check_requests())
    finally:
        event.remove(session_factory.class_, "before_commit", before_commit)


@pytest.mark.parametrize("path", ["/v2/assets", f"/v2/assets/{ASSET_ID}", f"/{ASSET_ID}"])
@pytest.mark.parametrize("conditional", [False, True])
def test_slow_asset_response_does_not_block_writes(session_factory, asset_cache_client, path, conditional):
    with session_factory() as session:
        register_v2_asset(session, v2_request())
    backdate_registry_version(session_factory)
    date = asset_cache_client.get(path).headers["Last-Modified"]

    def write_asset():
        with session_factory() as session:
            session.execute(text("set local lock_timeout = '2s'"))
            session.execute(
                text("update assets set name = 'Changed during delivery' where asset_id = :asset_id"),
                {"asset_id": ASSET_ID},
            )
            session.commit()

    async def check_response():
        body_started = asyncio.Event()
        release_body = asyncio.Event()
        statuses = []

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])
            elif message["type"] == "http.response.body":
                body_started.set()
                await release_body.wait()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(b"if-modified-since", date.encode())] if conditional else [],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        }
        response = asyncio.create_task(asset_cache_client.app(scope, receive, send))
        try:
            await asyncio.wait_for(body_started.wait(), timeout=5)
            assert statuses == [304 if conditional else 200]
            await asyncio.wait_for(asyncio.to_thread(write_asset), timeout=5)
            assert not response.done(), "Response delivery should still be stalled"
        finally:
            release_body.set()
            await asyncio.wait_for(response, timeout=5)

    asyncio.run(check_response())
    assert asset_cache_client.get(f"/v2/assets/{ASSET_ID}").json()["contract"]["name"] == "Changed during delivery"
