from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from registry_api.api import v2 as v2_api
from registry_api.db import get_db
from registry_api.errors import RegistryError
from registry_api.main import create_app
from registry_api.schemas import AssetListResponse
from registry_api.v2_assets import _filtered_asset_query, search_v2_assets


def test_search_assets_api_accepts_mixed_case_filter_values(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_search(_db: Session, **filters: object) -> AssetListResponse:
        captured.update(filters)
        return AssetListResponse(
            items=[], page=1, page_size=50, total_count=0, total_pages=0
        )

    monkeypatch.setattr(v2_api, "search_v2_assets", fake_search)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: object()

    response = TestClient(app).get(
        "/v2/assets",
        params={
            "asset_id": "DD90",
            "domain": "Proof.Example.COM",
            "ticker": "sAt",
            "name": "SaToShI",
            "asset_type": "amp_ASSET",
            "category_tag": "BoNd",
            "trading_venue": "BiTfInEx",
        },
    )

    assert response.status_code == 200
    assert captured["asset_type"] == "AMP_asset"
    assert captured["category_tag"] == ["bond"]
    assert captured["trading_venue"] == "bitfinex"


def test_search_assets_uses_lowercase_prefix_predicates() -> None:
    compiled = _filtered_asset_query(name="SaToShI", ticker="sAt").compile(
        dialect=postgresql.dialect()
    )

    statement = str(compiled)
    assert "lower(assets.name) LIKE" in statement
    assert "lower(assets.ticker) LIKE" in statement
    assert {"sat%", "satoshi%"}.issubset(compiled.params.values())


def test_search_assets_rejects_bad_trading_venue_with_available_values() -> None:
    with pytest.raises(RegistryError) as exc_info:
        search_v2_assets(cast(Session, None), trading_venue="unknown")

    assert exc_info.value.error == "validation_error"
    assert exc_info.value.message == "unsupported trading venue"
    assert exc_info.value.details == {
        "available_trading_venues": ["bitfinex", "sideswap"]
    }
