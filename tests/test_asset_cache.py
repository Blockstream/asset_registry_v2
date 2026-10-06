from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from registry_api.api import legacy, v2
from registry_api.db import get_db
from registry_api.errors import RegistryError
from registry_api.main import create_app
from registry_api.schemas import AssetListResponse, AssetResponse

ASSET_ID = "ab" * 32
PUBKEY = "02" + "11" * 32
MODIFIED = datetime(2026, 1, 1, 12, 0, 0, 987654, tzinfo=UTC)
HTTP_DATE = "Thu, 01 Jan 2026 12:00:00 GMT"
PATHS = ["/", "/index.json", f"/{ASSET_ID}", "/v2/assets", "/v2/assets/all.json", f"/v2/assets/{ASSET_ID}"]


@pytest.fixture()
def cached_client(monkeypatch):
    asset = AssetResponse.model_validate(
        {
            "asset_id": ASSET_ID,
            "contract": {"entity": {"domain": "example.com"}, "name": "Cache Asset", "precision": 8, "version": 2},
            "initial_issuer_pubkey": PUBKEY,
            "initial_issuer_pubkey_source": "contract",
            "current_issuer_pubkey": PUBKEY,
            "mutable": {},
            "icon": None,
            "status": "active",
            "created_at": MODIFIED,
            "updated_at": MODIFIED,
        }
    )
    lookup = Mock(return_value=asset)
    search = Mock(return_value=AssetListResponse(items=[asset], page=1, page_size=50, total_count=1))
    legacy_lookup = Mock(return_value={"asset_id": ASSET_ID})
    legacy_stream = Mock(side_effect=lambda: iter([b"{} "]))
    v2_stream = Mock(side_effect=lambda: iter([b"{} "]))
    monkeypatch.setattr(v2, "get_v2_asset", lookup)
    monkeypatch.setattr(v2, "search_v2_assets", search)
    monkeypatch.setattr(legacy, "get_legacy_asset", legacy_lookup)
    monkeypatch.setattr(legacy, "stream_legacy_all_json_bytes", legacy_stream)
    monkeypatch.setattr(v2, "stream_v2_all_json_bytes", v2_stream)
    db = Mock()
    db.scalar.return_value = MODIFIED
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, db, [lookup, search, legacy_lookup, legacy_stream, v2_stream]


@pytest.mark.parametrize("path", PATHS)
def test_asset_reads_send_http_date_and_return_bodyless_304(cached_client, path):
    client, _, readers = cached_client
    original = client.get(path)
    assert original.status_code == 200
    assert original.headers["Last-Modified"] == HTTP_DATE
    assert original.headers["Cache-Control"] == "no-cache"
    assert "Accept-Encoding" in original.headers["Vary"]
    for reader in readers:
        reader.reset_mock()

    cached = client.get(path, headers={"If-Modified-Since": original.headers["Last-Modified"]})
    assert cached.status_code == 304
    assert cached.content == b""
    assert "content-length" not in cached.headers
    assert "content-type" not in cached.headers
    assert "content-encoding" not in cached.headers
    assert cached.headers["Last-Modified"] == HTTP_DATE
    assert cached.headers["Cache-Control"] == "no-cache"
    assert "Accept-Encoding" in cached.headers["Vary"]
    for reader in readers:
        reader.assert_not_called()


@pytest.mark.parametrize("path", PATHS)
def test_stale_date_returns_asset_body(cached_client, path):
    client, _, _ = cached_client
    result = client.get(path, headers={"If-Modified-Since": format_datetime(MODIFIED - timedelta(seconds=1), usegmt=True)})
    assert result.status_code == 200
    assert result.content


@pytest.mark.parametrize("value", [
    "invalid", "", "2026-01-01T12:00:00Z", "Thu, 32 Jan 2026 12:00:00 GMT",
    f"{HTTP_DATE}, {HTTP_DATE}", f"{HTTP_DATE} junk", "Thu, 01 Jan 2026 25:00:00 GMT",
])
def test_invalid_http_dates_are_ignored(cached_client, value):
    client, _, _ = cached_client
    assert client.get("/index.json", headers={"If-Modified-Since": value}).status_code == 200


@pytest.mark.parametrize("value", [
    "Thursday, 01-Jan-26 12:00:00 GMT", "Thu Jan  1 12:00:00 2026",
    "Thu, 01 Jan 2026 12:00:01 GMT",
])
def test_obsolete_formats_and_later_dates_are_supported(cached_client, value):
    client, _, _ = cached_client
    assert client.get("/index.json", headers={"If-Modified-Since": value}).status_code == 304


def test_repeated_dates_are_ignored(cached_client):
    client, _, _ = cached_client
    result = client.get("/index.json", headers=[("If-Modified-Since", HTTP_DATE), ("If-Modified-Since", HTTP_DATE)])
    assert result.status_code == 200


@pytest.mark.parametrize("path", PATHS)
def test_if_none_match_takes_precedence(cached_client, path):
    client, _, _ = cached_client
    result = client.get(path, headers={"If-Modified-Since": HTTP_DATE, "If-None-Match": '"other"'})
    assert result.status_code == 200


@pytest.mark.parametrize("path", ["/", "/index.json", "/v2/assets", "/v2/assets/all.json"])
def test_unknown_modification_time_does_not_return_304(cached_client, path):
    client, db, _ = cached_client
    db.scalar.return_value = None
    result = client.get(path, headers={"If-Modified-Since": HTTP_DATE})
    assert result.status_code == 200
    assert "Last-Modified" not in result.headers


@pytest.mark.parametrize("path, reader_index", [(f"/{ASSET_ID}", 2), (f"/v2/assets/{ASSET_ID}", 0)])
def test_missing_assets_still_return_404(cached_client, path, reader_index):
    client, db, readers = cached_client
    db.scalar.return_value = None
    readers[reader_index].side_effect = RegistryError("asset_not_found", "asset not found", status_code=404)
    result = client.get(path, headers={"If-Modified-Since": HTTP_DATE})
    assert result.status_code == 404
    assert "Last-Modified" not in result.headers


@pytest.mark.parametrize("path", ["/index.json?unknown=1", "/v2/assets?page=0", "/v2/assets?sort=invalid"])
def test_conditional_requests_preserve_request_validation(cached_client, path):
    client, _, _ = cached_client
    assert client.get(path, headers={"If-Modified-Since": HTTP_DATE}).status_code in {400, 422}


@pytest.mark.parametrize("path", PATHS)
def test_openapi_describes_date_header_and_304(cached_client, path):
    client, _, _ = cached_client
    path = path.replace(ASSET_ID, "{asset_id}")
    operation = client.get("/openapi.json").json()["paths"][path]["get"]
    assert any(p["name"] == "If-Modified-Since" and p["in"] == "header" for p in operation["parameters"])
    assert "Last-Modified" in operation["responses"]["200"]["headers"]
    assert "content" not in operation["responses"]["304"]
