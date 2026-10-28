import asyncio
import json
from threading import Event
from unittest.mock import Mock

import httpx
import pytest
from fastapi import Request, Response
from fastapi.testclient import TestClient
from psycopg.errors import LockNotAvailable
from sqlalchemy.exc import OperationalError

from registry_api.api import v2
from registry_api.db import get_db
from registry_api.main import create_app
from registry_api.openapi_metadata import (
    ADMIN_ANNOTATION_EXAMPLES,
    ADMIN_ASSET_EXAMPLES,
    ADMIN_ICON_SEARCH_EXAMPLES,
    ADMIN_LIFECYCLE_EXAMPLES,
    ADMIN_PUBKEY,
    ASSET_ID,
    ISSUER_ACTION_EXAMPLES,
    ISSUER_ICON_SEARCH_EXAMPLES,
)
from registry_api.schemas import AssetListResponse


@pytest.mark.parametrize(
    "method,path,service,action",
    [
        pytest.param(
            "POST",
            f"/v2/assets/{ASSET_ID}/actions",
            "submit_issuer_action",
            ISSUER_ACTION_EXAMPLES["replaceCategoryTags"]["value"],
            id="issuer-action",
        ),
        pytest.param(
            "POST",
            f"/v2/assets/{ASSET_ID}/icon-proposals/search",
            "search_issuer_icon_proposals",
            ISSUER_ICON_SEARCH_EXAMPLES["newestFirst"]["value"],
            id="issuer-search",
        ),
        pytest.param(
            "POST",
            f"/v2/assets/{ASSET_ID}/migrate",
            "migrate_legacy_asset_to_v2",
            {
                "actor_pubkey": ADMIN_PUBKEY,
                "asset_id": ASSET_ID,
                "nonce": "async-migration",
                "operation": "migrate_asset",
                "signing_context": "liquid-asset-registry-admin-action-v1",
                "timestamp": "2026-04-30T15:34:00.123Z",
            },
            id="migration",
        ),
        pytest.param(
            "POST",
            "/v2/admin/actions",
            "submit_admin_lifecycle_action",
            ADMIN_LIFECYCLE_EXAMPLES["addAdmin"]["value"],
            id="admin-lifecycle",
        ),
        pytest.param(
            "POST",
            "/v2/admin/icon-proposals/search",
            "search_pending_icon_proposals",
            ADMIN_ICON_SEARCH_EXAMPLES["oldestFirst"]["value"],
            id="admin-search",
        ),
        pytest.param(
            "PUT",
            f"/v2/admin/assets/{ASSET_ID}/annotations",
            "update_admin_annotations",
            ADMIN_ANNOTATION_EXAMPLES["markFeatured"]["value"],
            id="admin-annotations",
        ),
        pytest.param(
            "POST",
            f"/v2/admin/assets/{ASSET_ID}/actions",
            "submit_admin_asset_action",
            ADMIN_ASSET_EXAMPLES["forceDelist"]["value"],
            id="admin-asset-action",
        ),
        pytest.param(
            "POST",
            f"/v2/admin/assets/{ASSET_ID}/actions",
            "decide_icon_proposal",
            ADMIN_ASSET_EXAMPLES["approveIcon"]["value"],
            id="approve-icon",
        ),
        pytest.param(
            "POST",
            f"/v2/admin/assets/{ASSET_ID}/actions",
            "decide_icon_proposal",
            ADMIN_ASSET_EXAMPLES["rejectIcon"]["value"],
            id="reject-icon",
        ),
    ],
)
def test_blocking_service_allows_health_and_reader_teardown(
    monkeypatch, method, path, service, action
):
    """Reproduce the commit/reader-teardown cycle without a database lock timeout.

    A reader holds its simulated lock until dependency teardown. The synchronous
    service waits for that teardown, just as a commit waits for FOR SHARE. A
    bounded thread wait makes the old event-loop deadlock fail instead of hanging
    the test process.
    """
    reader_started = Event()
    service_started = Event()
    finish_read = Event()
    reader_closed = Event()
    service_db = Mock()
    payload = json.dumps(action, indent=2).encode()
    signature = "test-signature"

    def test_db(request: Request):
        if request.method == "GET":
            db = Mock()
            db.execute.return_value.one_or_none.return_value = None
            try:
                yield db
            finally:
                reader_closed.set()
        else:
            yield service_db

    def blocking_read(_db, **_filters):
        reader_started.set()
        assert finish_read.wait(timeout=5), "Reader was not released"
        return AssetListResponse(
            items=[], page=1, page_size=50, total_count=0, total_pages=0
        )

    def blocking_service(db, *args, **kwargs):
        assert db is service_db
        assert kwargs["payload"] == payload
        assert kwargs["signature"] == signature
        if "asset_id" in action:
            assert kwargs.get("asset_id", args[0] if args else None) == ASSET_ID
        service_started.set()
        assert reader_closed.wait(timeout=5), (
            "Event loop could not release the reader's lock"
        )
        return Response(status_code=204)

    monkeypatch.setattr(v2, "search_v2_assets", blocking_read)
    monkeypatch.setattr(v2, service, blocking_service)
    app = create_app()
    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[v2.ensure_genesis_admin] = lambda: None

    async def check_requests():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            reader = asyncio.create_task(client.get("/v2/assets"))
            tasks = [reader]
            try:
                assert await asyncio.to_thread(reader_started.wait, 5), (
                    "Reader did not start"
                )
                writer = asyncio.create_task(
                    client.request(
                        method,
                        path,
                        content=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Asset-Registry-Signature": signature,
                            "Asset-Registry-Admin-Signature": signature,
                        },
                    )
                )
                tasks.append(writer)
                assert await asyncio.to_thread(service_started.wait, 5), (
                    "Service did not start"
                )
                assert not writer.done(), (
                    "Service should still be waiting for reader teardown"
                )
                health = await asyncio.wait_for(client.get("/health"), timeout=2)
                assert health.status_code == 200
                assert not reader_closed.is_set()
                finish_read.set()
                read_response, write_response = await asyncio.wait_for(
                    asyncio.gather(reader, writer), timeout=5
                )
                assert read_response.status_code == 200
                assert write_response.status_code == 204
                assert reader_closed.is_set()
            finally:
                finish_read.set()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(check_requests())


@pytest.mark.parametrize(
    "orig,status", [(LockNotAvailable("lock timeout"), 503), (Exception("down"), 500)]
)
def test_lock_timeout_returns_retryable_503(monkeypatch, orig, status):
    def fail(*_args, **_kwargs):
        raise OperationalError("update", {}, orig)

    monkeypatch.setattr(v2, "update_admin_annotations", fail)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: Mock()
    app.dependency_overrides[v2.ensure_genesis_admin] = lambda: None
    payload = json.dumps(ADMIN_ANNOTATION_EXAMPLES["markFeatured"]["value"])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            f"/v2/admin/assets/{ASSET_ID}/annotations",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "Asset-Registry-Admin-Signature": "test-signature",
            },
        )

    assert response.status_code == status
    if status == 503:
        assert response.headers["Retry-After"] == "1"
        assert response.json()["error"] == "database_busy"
