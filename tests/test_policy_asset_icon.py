import base64
import hashlib
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from registry_api import db as db_module
from registry_api.constants import LIQUID_MAINNET_POLICY_ASSET_ID
from registry_api.icons import (
    decode_legacy_icon,
    liquid_mainnet_policy_asset_icon_base64,
)
from registry_api.main import create_app
from registry_api.settings import Settings, get_settings


def test_bundled_liquid_policy_asset_icon_is_a_valid_legacy_icon() -> None:
    encoded = liquid_mainnet_policy_asset_icon_base64()

    image_data, icon_hash, deviations = decode_legacy_icon(encoded)

    assert base64.b64encode(image_data).decode("ascii") == encoded
    assert icon_hash == hashlib.sha256(image_data).hexdigest()
    assert deviations == ["dimensions"]


@pytest.mark.parametrize(
    ("network", "includes_policy_asset"),
    [("liquid", True), ("liquidtestnet", False)],
)
def test_legacy_icons_policy_asset_fallback_is_liquid_mainnet_only(
    monkeypatch, network: str, includes_policy_asset: bool
) -> None:
    _stub_streamed_icon_rows(monkeypatch, [])
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(network=network)

    response = TestClient(app).get("/icons.json")

    assert response.status_code == 200
    icons = response.json()
    assert (LIQUID_MAINNET_POLICY_ASSET_ID in icons) is includes_policy_asset
    if includes_policy_asset:
        assert (
            icons[LIQUID_MAINNET_POLICY_ASSET_ID]
            == liquid_mainnet_policy_asset_icon_base64()
        )


def test_database_policy_asset_icon_takes_precedence_and_preserves_order(
    monkeypatch,
) -> None:
    lower_asset_id = "0" * 64
    higher_asset_id = "f" * 64
    database_image = b"database policy asset icon"
    _stub_streamed_icon_rows(
        monkeypatch,
        [
            (lower_asset_id, b"lower icon"),
            (LIQUID_MAINNET_POLICY_ASSET_ID, database_image),
            (higher_asset_id, b"higher icon"),
        ],
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(network="liquid")

    response = TestClient(app).get("/icons.json")

    assert response.status_code == 200
    icons = response.json()
    assert list(icons) == [
        lower_asset_id,
        LIQUID_MAINNET_POLICY_ASSET_ID,
        higher_asset_id,
    ]
    assert icons[LIQUID_MAINNET_POLICY_ASSET_ID] == base64.b64encode(
        database_image
    ).decode("ascii")


def _stub_streamed_icon_rows(monkeypatch, rows: list[tuple[str, bytes]]) -> None:
    session = MagicMock()
    session.__enter__.return_value = session
    # The endpoint streams via a server-side cursor (yield_per is an execution
    # option on the statement), so it iterates the Result that execute() returns.
    session.execute.return_value = iter(rows)
    monkeypatch.setattr(db_module, "SessionLocal", lambda: session)
