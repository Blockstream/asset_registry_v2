import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DataError, IntegrityError


pytestmark = pytest.mark.skipif(
    not os.getenv("ASSET_REGISTRY_TEST_DATABASE_URL"),
    reason="ASSET_REGISTRY_TEST_DATABASE_URL is required for PostgreSQL constraint tests",
)

VALID_ASSET_ID = "aa909f1b00000000000000000000000000000000000000000000000000000000"
VALID_ASSET_ID_2 = "bb909f1b00000000000000000000000000000000000000000000000000000000"
VALID_PUBKEY = "0382375b3986feb6f33d96f86c4bc5e09f53d7b3e4eb5b90eeca6d487b7eb40a65"
VALID_ADMIN_PUBKEY = "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"


@pytest.fixture()
def engine():
    engine = create_engine(os.environ["ASSET_REGISTRY_TEST_DATABASE_URL"])
    try:
        yield engine
    finally:
        engine.dispose()


def insert_asset_sql(**overrides: object):
    values = {
        "asset_id": VALID_ASSET_ID,
        "contract_version": 2,
        "domain": "proof.example.com",
        "name": "Example Asset",
        "ticker": "EXAMPLE",
        "precision": 8,
        "initial_issuer_pubkey": VALID_PUBKEY,
        "initial_issuer_pubkey_source": "contract",
        "current_issuer_pubkey": VALID_PUBKEY,
        "status": "active",
    }
    values.update(overrides)
    return (
        text(
            """
            insert into assets (
              asset_id, contract_version, domain, name, ticker, precision,
              initial_issuer_pubkey, initial_issuer_pubkey_source, current_issuer_pubkey, status
            ) values (
              :asset_id, :contract_version, :domain, :name, :ticker, :precision,
              :initial_issuer_pubkey, :initial_issuer_pubkey_source, :current_issuer_pubkey, :status
            )
            """
        ),
        values,
    )


def test_assets_accept_valid_minimal_row(engine) -> None:
    statement, values = insert_asset_sql()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(statement, values)
        finally:
            transaction.rollback()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("asset_id", "not-hex"),
        ("initial_issuer_pubkey", "04" + "00" * 32),
        ("current_issuer_pubkey", "04" + "00" * 32),
    ],
)
def test_assets_reject_invalid_format_values(engine, field: str, value: str) -> None:
    statement, values = insert_asset_sql(**{field: value})
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(statement, values)


def test_uuid_columns_reject_malformed_uuid_values(engine) -> None:
    with pytest.raises(DataError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into asset_mutable_metadata (asset_uuid, schema_version)
                    values (:asset_uuid, 1)
                    """
                ),
                {"asset_uuid": "not-a-uuid"},
            )


def test_one_active_asset_id_constraint_rejects_duplicate_active_asset(engine) -> None:
    first_statement, first_values = insert_asset_sql(asset_id=VALID_ASSET_ID_2)
    second_statement, second_values = insert_asset_sql(asset_id=VALID_ASSET_ID_2, ticker="EXAMPLE2")
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(first_statement, first_values)
            with pytest.raises(IntegrityError):
                connection.execute(second_statement, second_values)
        finally:
            transaction.rollback()


def test_domain_ticker_constraint_rejects_duplicate_active_non_null_ticker(engine) -> None:
    first_statement, first_values = insert_asset_sql(asset_id=VALID_ASSET_ID)
    second_statement, second_values = insert_asset_sql(asset_id=VALID_ASSET_ID_2)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(first_statement, first_values)
            with pytest.raises(IntegrityError):
                connection.execute(second_statement, second_values)
        finally:
            transaction.rollback()


def test_domain_ticker_constraint_allows_duplicate_active_case_variant_ticker(engine) -> None:
    first_statement, first_values = insert_asset_sql(asset_id=VALID_ASSET_ID, ticker="EXAMPLE")
    second_statement, second_values = insert_asset_sql(asset_id=VALID_ASSET_ID_2, ticker="example")
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(first_statement, first_values)
            connection.execute(second_statement, second_values)
        finally:
            transaction.rollback()


def test_domain_ticker_constraint_allows_duplicate_active_null_ticker(engine) -> None:
    first_statement, first_values = insert_asset_sql(asset_id=VALID_ASSET_ID, ticker=None)
    second_statement, second_values = insert_asset_sql(asset_id=VALID_ASSET_ID_2, ticker=None)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(first_statement, first_values)
            connection.execute(second_statement, second_values)
        finally:
            transaction.rollback()


def test_global_audit_sequence_orders_asset_and_admin_actions(engine) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _statement, values = insert_asset_sql(asset_id=VALID_ASSET_ID_2)
            asset_uuid = connection.execute(
                text(
                    """
                    insert into assets (
                      asset_id, contract_version, domain, name, ticker, precision,
                      initial_issuer_pubkey, initial_issuer_pubkey_source, current_issuer_pubkey, status
                    ) values (
                      :asset_id, :contract_version, :domain, :name, :ticker, :precision,
                      :initial_issuer_pubkey, :initial_issuer_pubkey_source, :current_issuer_pubkey, :status
                    )
                    returning asset_uuid
                    """
                ),
                values,
            ).scalar_one()
            asset_audit_sequence = connection.execute(
                text(
                    """
                    insert into actions (asset_uuid, asset_chain_id, actor, operation, action)
                    values (:asset_uuid, :asset_chain_id, 'system', 'test_asset_action', '{}'::jsonb)
                    returning audit_sequence
                    """
                ),
                {"asset_uuid": asset_uuid, "asset_chain_id": VALID_ASSET_ID_2},
            ).scalar_one()
            admin_uuid = connection.execute(
                text(
                    """
                    insert into admin_keys (pubkey, friendly_name, status)
                    values (:pubkey, 'Genesis', 'active')
                    returning admin_uuid
                    """
                ),
                {"pubkey": VALID_ADMIN_PUBKEY},
            ).scalar_one()
            admin_audit_sequence = connection.execute(
                text(
                    """
                    insert into admin_actions (
                      actor_admin_uuid, actor_pubkey, operation, action, signature, nonce, admin_timestamp
                    ) values (
                      :admin_uuid, :pubkey, 'add_admin', '{}'::jsonb, 'signature', 'nonce-1', now()
                    )
                    returning audit_sequence
                    """
                ),
                {"admin_uuid": admin_uuid, "pubkey": VALID_ADMIN_PUBKEY},
            ).scalar_one()
        finally:
            transaction.rollback()

    assert admin_audit_sequence > asset_audit_sequence


def test_admin_tables_reject_invalid_pubkeys_and_duplicate_nonces(engine) -> None:
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into admin_keys (pubkey, friendly_name, status)
                    values ('04bad', 'Bad Key', 'active')
                    """
                )
            )

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            admin_uuid = connection.execute(
                text(
                    """
                    insert into admin_keys (pubkey, friendly_name, status)
                    values (:pubkey, 'Genesis', 'active')
                    returning admin_uuid
                    """
                ),
                {"pubkey": VALID_ADMIN_PUBKEY},
            ).scalar_one()
            statement = text(
                """
                insert into admin_actions (
                  actor_admin_uuid, actor_pubkey, operation, action, signature, nonce, admin_timestamp
                ) values (
                  :admin_uuid, :pubkey, 'add_admin', '{}'::jsonb, 'signature', 'nonce-1', now()
                )
                """
            )
            connection.execute(statement, {"admin_uuid": admin_uuid, "pubkey": VALID_ADMIN_PUBKEY})
            with pytest.raises(IntegrityError):
                connection.execute(statement, {"admin_uuid": admin_uuid, "pubkey": VALID_ADMIN_PUBKEY})
        finally:
            transaction.rollback()
