import logging
import os

import pytest
import wallycore as wally
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from registry_api.canonical_json import contract_hash
from registry_api.chain import derive_asset_id
from registry_api.legacy_assets import get_legacy_asset
from registry_api.legacy_import import (
    LegacyImportItem,
    LegacyImportSummary,
    import_legacy_assets,
    legacy_request_from_listing_item,
    main,
)
from registry_api.registration import register_legacy_asset
from registry_api.schemas import LegacyAssetRequest


ASSET_ID = "aa909f1b00000000000000000000000000000000000000000000000000000000"
ASSET_ID_2 = "ab909f1b00000000000000000000000000000000000000000000000000000000"
ASSET_ID_3 = "ac909f1b00000000000000000000000000000000000000000000000000000000"
PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
UNCOMPRESSED_PUBKEY = wally.ec_public_key_decompress(bytes.fromhex(PUBKEY)).hex()


@pytest.fixture()
def session_factory():
    if not os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL"):
        pytest.skip("ASSET_REGISTRY_TEST_DATABASE_URL is required for legacy import tests")
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


def legacy_listing_item(
    asset_id: str,
    *,
    domain: str = "legacy.example.com",
    ticker: str | None = "LEGACY",
    name: str = "Legacy Import Asset",
) -> dict:
    contract = {
        "entity": {"domain": domain},
        "issuer_pubkey": PUBKEY,
        "name": name,
        "precision": 0,
        "version": 0,
        "issuer_identifier": "issuer-123",
    }
    if ticker is not None:
        contract["ticker"] = ticker
    return {
        "asset_id": asset_id,
        "contract": contract,
        "issuance_txin": {"txid": "00" * 32, "vin": 0},
        "issuance_prevout": {"txid": "11" * 32, "vout": 1},
        "version": 0,
        "issuer_pubkey": PUBKEY,
        "name": name,
        "ticker": ticker,
        "precision": 0,
        "entity": {"domain": domain},
    }


def legacy_request(asset_id: str, *, domain: str = "legacy.example.com", ticker: str | None = "LEGACY") -> LegacyAssetRequest:
    contract = {
        "entity": {"domain": domain},
        "issuer_pubkey": PUBKEY,
        "name": "Existing Asset",
        "precision": 0,
        "version": 0,
    }
    if ticker is not None:
        contract["ticker"] = ticker
    return LegacyAssetRequest.model_validate(
        {
            "asset_id": asset_id,
            "contract": contract,
        }
    )


def test_legacy_listing_item_does_not_fill_contract_from_top_level_fields() -> None:
    item = legacy_listing_item(ASSET_ID)
    del item["contract"]["ticker"]

    request, response = legacy_request_from_listing_item(
        ASSET_ID, item, verify_imported_contract_identity=False
    )

    assert request.asset_id == ASSET_ID
    assert request.contract.issuer_pubkey == PUBKEY
    assert request.contract.ticker is None
    assert response["contract"]["issuer_pubkey"] == PUBKEY
    assert "ticker" not in response["contract"]
    assert response["ticker"] == "LEGACY"


@pytest.mark.parametrize("collection", [None, "Top-level collection"])
def test_legacy_listing_item_does_not_fill_contract_collection_from_top_level(
    collection: str | None,
) -> None:
    item = legacy_listing_item(ASSET_ID)
    item["collection"] = collection

    request, response = legacy_request_from_listing_item(
        ASSET_ID, item, verify_imported_contract_identity=False
    )

    assert request.contract.collection is None
    assert "collection" not in response["contract"]
    assert response["collection"] == collection


def test_legacy_listing_item_preserves_collection_present_in_contract() -> None:
    item = legacy_listing_item(ASSET_ID)
    item["collection"] = None
    item["contract"]["collection"] = "Contract collection"

    request, response = legacy_request_from_listing_item(
        ASSET_ID, item, verify_imported_contract_identity=False
    )

    assert request.contract.collection == "Contract collection"
    assert response["contract"]["collection"] == "Contract collection"
    assert response["collection"] is None


def test_legacy_listing_item_compresses_uncompressed_contract_issuer_pubkey() -> None:
    item = legacy_listing_item(ASSET_ID)
    item["contract"]["issuer_pubkey"] = UNCOMPRESSED_PUBKEY.upper()
    item["issuer_pubkey"] = UNCOMPRESSED_PUBKEY.upper()

    request, response = legacy_request_from_listing_item(
        ASSET_ID, item, verify_imported_contract_identity=False
    )

    assert request.contract.issuer_pubkey == PUBKEY
    assert response["contract"]["issuer_pubkey"] == UNCOMPRESSED_PUBKEY.upper()
    assert response["issuer_pubkey"] == UNCOMPRESSED_PUBKEY.upper()


def test_legacy_listing_item_rejects_top_level_issuer_pubkey_fallback() -> None:
    item = legacy_listing_item(ASSET_ID)
    del item["contract"]["issuer_pubkey"]
    item["issuer_pubkey"] = UNCOMPRESSED_PUBKEY

    with pytest.raises(ValidationError, match="issuer_pubkey"):
        legacy_request_from_listing_item(
            ASSET_ID, item, verify_imported_contract_identity=False
        )


def test_legacy_listing_item_verifies_registered_contract_identity() -> None:
    item = legacy_listing_item(ASSET_ID)
    item["collection"] = None
    prevout = item["issuance_prevout"]
    asset_id = derive_asset_id(
        prevout["txid"],
        prevout["vout"],
        contract_hash(item["contract"]),
    )
    item["asset_id"] = asset_id

    request, response = legacy_request_from_listing_item(asset_id, item)

    assert request.asset_id == asset_id
    assert "collection" not in response["contract"]


def test_legacy_listing_item_rejects_contract_that_does_not_derive_asset_id() -> None:
    item = legacy_listing_item(ASSET_ID)

    with pytest.raises(
        ValueError, match="legacy asset contract does not derive the listed asset_id"
    ):
        legacy_request_from_listing_item(ASSET_ID, item)


def test_import_legacy_assets_logs_progress_and_delays_every_interval(caplog) -> None:
    delays = []
    payload = {"bad-1": None, "bad-2": None, "bad-3": None}

    with caplog.at_level(logging.INFO, logger="registry_api.legacy_import"):
        summary = import_legacy_assets(
            object(),
            payload,
            progress_interval=2,
            delay_seconds=1.25,
            sleep=delays.append,
            verify_imported_contract_identity=False,
        )

    assert summary.total == 3
    assert summary.invalid == 3
    assert delays == [1.25]
    assert "starting legacy asset import" in caplog.text
    assert "legacy asset import progress: processed=2 total=3" in caplog.text
    assert "pausing legacy asset import: processed=2 total=3 delay_seconds=1.25" in caplog.text
    assert "finished legacy asset import" in caplog.text


def test_import_summary_counts_successful_and_failed_migrations() -> None:
    summary = LegacyImportSummary(total=4)
    summary.add(LegacyImportItem(ASSET_ID, "imported"))
    summary.add(LegacyImportItem(ASSET_ID_2, "skipped_existing_asset_id"))
    summary.add(LegacyImportItem(ASSET_ID_3, "skipped_namespace_conflict"))
    summary.add(LegacyImportItem("bad", "invalid"))

    assert summary.successful_migrations == 1
    assert summary.failed_migrations == 3


def test_legacy_import_cli_prints_successful_and_failed_migrations_last(tmp_path, monkeypatch, capsys) -> None:
    json_path = tmp_path / "legacy.json"
    json_path.write_text('{"bad": null}', encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["import_legacy_assets.py", str(json_path), "--show-items", "--delay-seconds", "0"],
    )

    exit_code = main()

    assert exit_code == 1
    assert capsys.readouterr().out.strip().splitlines()[-1] == "successful_migrations=0 failed_migrations=1"


def test_import_legacy_assets_imports_and_preserves_listing_payload(session_factory) -> None:
    payload = {ASSET_ID: legacy_listing_item(ASSET_ID)}

    with session_factory() as session:
        summary = import_legacy_assets(
            session, payload, verify_imported_contract_identity=False
        )

    assert summary.total == 1
    assert summary.imported == 1

    with session_factory() as session:
        imported = get_legacy_asset(session, ASSET_ID)
        stored_contract_hash = session.execute(
            text(
                "select action->>'contract_hash' from actions "
                "where operation = 'legacy_register'"
            )
        ).scalar_one()

    assert imported["asset_id"] == ASSET_ID
    assert imported["issuance_txin"] == {"txid": "00" * 32, "vin": 0}
    assert imported["contract"]["issuer_identifier"] == "issuer-123"
    assert imported["contract"] == payload[ASSET_ID]["contract"]
    assert stored_contract_hash == contract_hash(payload[ASSET_ID]["contract"])


def test_import_legacy_assets_allows_same_domain_with_null_tickers(session_factory) -> None:
    payload = {
        ASSET_ID: legacy_listing_item(
            ASSET_ID,
            domain="shared.example.com",
            ticker=None,
            name="First Null Ticker",
        ),
        ASSET_ID_2: legacy_listing_item(
            ASSET_ID_2,
            domain="shared.example.com",
            ticker=None,
            name="Second Null Ticker",
        ),
    }

    with session_factory() as session:
        summary = import_legacy_assets(
            session, payload, verify_imported_contract_identity=False
        )

    assert summary.total == 2
    assert summary.imported == 2
    assert summary.successful_migrations == 2
    assert summary.failed_migrations == 0

    with session_factory() as session:
        rows = session.execute(
            text(
                """
                select asset_id, domain, ticker
                from assets
                where asset_id in (:first_asset_id, :second_asset_id)
                order by asset_id
                """
            ),
            {"first_asset_id": ASSET_ID, "second_asset_id": ASSET_ID_2},
        ).mappings().all()

    assert [dict(row) for row in rows] == [
        {"asset_id": ASSET_ID, "domain": "shared.example.com", "ticker": None},
        {"asset_id": ASSET_ID_2, "domain": "shared.example.com", "ticker": None},
    ]


def test_import_legacy_assets_dry_run_counts_would_import_without_writing(session_factory) -> None:
    payload = {ASSET_ID: legacy_listing_item(ASSET_ID)}

    with session_factory() as session:
        summary = import_legacy_assets(
            session, payload, dry_run=True, verify_imported_contract_identity=False
        )
        asset_count = session.execute(text("select count(*) from assets")).scalar_one()

    assert summary.total == 1
    assert summary.imported == 0
    assert summary.would_import == 1
    assert asset_count == 0


def test_import_legacy_assets_skips_existing_asset_id_and_namespace_conflict(session_factory) -> None:
    with session_factory() as session:
        register_legacy_asset(session, legacy_request(ASSET_ID, domain="existing.example.com", ticker="EXIST"))
        register_legacy_asset(session, legacy_request(ASSET_ID_2, domain="taken.example.com", ticker="TAKEN"))

        summary = import_legacy_assets(
            session,
            {
                ASSET_ID: legacy_listing_item(ASSET_ID, domain="new.example.com", ticker="NEW"),
                ASSET_ID_3: legacy_listing_item(ASSET_ID_3, domain="taken.example.com", ticker="TAKEN"),
            },
            verify_imported_contract_identity=False,
        )

    assert summary.total == 2
    assert summary.imported == 0
    assert summary.skipped_existing_asset_id == 1
    assert summary.skipped_namespace_conflict == 1
    assert [item.status for item in summary.items] == [
        "skipped_existing_asset_id",
        "skipped_namespace_conflict",
    ]
