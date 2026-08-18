import base64
import json
import os
from datetime import UTC, datetime

import pytest
import wallycore as wally
from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import sessionmaker

from registry_api.canonical_json import canonical_json_bytes
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
from registry_api.v2_assets import all_v2_assets_json_bytes, get_v2_asset, register_v2_asset, search_v2_assets


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
