from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from registry_api.api import v2 as v2_api
from registry_api.db import get_db
from registry_api.main import create_app
from registry_api.schemas import AssetListResponse
from registry_api.v2_assets import _filtered_asset_query


def test_search_assets_api_accepts_mixed_case_filter_values(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_search(_db: Session, **filters: object) -> AssetListResponse:
        captured.update(filters)
        return AssetListResponse(
            items=[], page=1, page_size=50, total_count=0, total_pages=0
        )

    monkeypatch.setattr(v2_api, "search_v2_assets", fake_search)
    app = create_app()
    db = Mock(spec=Session)
    db.execute.return_value.one_or_none.return_value = None
    app.dependency_overrides[get_db] = lambda: db

    response = TestClient(app).get(
        "/v2/assets",
        params={
            "asset_id": "DD90",
            "domain": "Proof.Example.COM",
            "ticker": "sAt",
            "name": "SaToShI",
            "asset_type": "amp_ASSET",
        },
    )

    assert response.status_code == 200
    assert captured["asset_type"] == "AMP_asset"


def test_search_assets_uses_lowercase_prefix_predicates() -> None:
    compiled = _filtered_asset_query(name="SaToShI", ticker="sAt").compile(
        dialect=postgresql.dialect()
    )

    statement = str(compiled)
    assert "lower(assets.name) LIKE" in statement
    assert "lower(assets.ticker) LIKE" in statement
    assert {"sat%", "satoshi%"}.issubset(compiled.params.values())


@pytest.mark.parametrize(
    "params",
    [
        {"category_tag": "bond"},
        {"category_tag": ["bond", "stablecoin"]},
        {"trading_venue": "sideswap"},
        {"category_tag": "", "trading_venue": ""},
    ],
)
def test_search_assets_rejects_retired_filters_before_conditional_reads(
    monkeypatch, params
) -> None:
    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("retired filters must be rejected first")

    monkeypatch.setattr(v2_api, "prepare_asset_read", unexpected)
    monkeypatch.setattr(v2_api, "search_v2_assets", unexpected)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: Mock(spec=Session)

    response = TestClient(app).get(
        "/v2/assets",
        params=params,
        headers={"If-Modified-Since": "Wed, 21 Oct 2099 07:28:00 GMT"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "validation_error"
    assert body["details"] == {"parameters": sorted(params)}
