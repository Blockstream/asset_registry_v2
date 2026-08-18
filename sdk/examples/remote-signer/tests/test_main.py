import os

import pytest
from httpx import AsyncClient, ASGITransport

import app.main as main_module
from app.main import app

_TEST_TOKEN = "test-token-123"
_TEST_PRIVKEY_HEX = "0000000000000000000000000000000000000000000000000000000000000001"


@pytest.fixture(autouse=True)
def _set_env():
    """Set required env vars for every test."""
    os.environ["SIGNER_TOKEN"] = _TEST_TOKEN
    os.environ["PRIVATE_KEY_HEX"] = _TEST_PRIVKEY_HEX
    yield
    os.environ.pop("SIGNER_TOKEN", None)
    os.environ.pop("PRIVATE_KEY_HEX", None)


@pytest.mark.asyncio
async def test_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_sign_success():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/sign",
            json={"message_hex": "48656c6c6f2c20576f726c6421"},
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "signature_hex" in data
    assert len(data["signature_hex"]) == 130


@pytest.mark.asyncio
async def test_sign_missing_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/sign", json={"message_hex": "74657374"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sign_wrong_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/sign",
            json={"message_hex": "74657374"},
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sign_missing_message():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/sign",
            json={},
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
    assert resp.status_code == 422  # FastAPI Pydantic validation


@pytest.mark.asyncio
async def test_sign_empty_message():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/sign",
            json={"message_hex": ""},
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
    assert resp.status_code == 422  # Pydantic field validator rejects empty strings


@pytest.mark.asyncio
async def test_sign_decodes_hex_before_signing(monkeypatch):
    captured = {}

    def fake_sign_bytes(message: bytes, private_key: bytes) -> str:
        captured["message"] = message
        captured["private_key"] = private_key
        return "1b" + ("00" * 64)

    monkeypatch.setattr(main_module, "sign_bytes", fake_sign_bytes)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/sign",
            json={"message_hex": "7b2261223a317d"},
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"signature_hex": "1b" + ("00" * 64)}
    assert captured["message"] == b'{"a":1}'
    assert captured["private_key"] == bytes.fromhex(_TEST_PRIVKEY_HEX)


@pytest.mark.asyncio
async def test_sign_rejects_invalid_hex():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/sign",
            json={"message_hex": "not-json-payload-bytes"},
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
    assert resp.status_code == 422
