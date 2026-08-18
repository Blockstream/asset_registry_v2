from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from registry_api.errors import RegistryError
from registry_api.main import create_app


def test_health_check_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_schema_includes_health_route() -> None:
    client = TestClient(create_app())

    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/health" in response.json()["paths"]


def test_large_json_responses_are_gzipped_when_supported() -> None:
    client = TestClient(create_app())

    response = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"


def test_legacy_index_json_matches_root_listing(monkeypatch) -> None:
    from registry_api.api import legacy

    payload = b'{"asset-id":{"asset_id":"asset-id"}}'
    monkeypatch.setattr(legacy, "stream_legacy_all_json_bytes", lambda: iter([payload]))
    client = TestClient(create_app())

    root_response = client.get("/")
    index_response = client.get("/index.json")

    assert root_response.status_code == 200
    assert index_response.status_code == 200
    assert index_response.content == root_response.content == payload
    assert index_response.headers["content-type"] == root_response.headers["content-type"]


def test_registry_errors_use_openapi_error_shape() -> None:
    app = create_app()

    @app.get("/test-error")
    def test_error():
        raise RegistryError("example_error", "Example message", {"field": "value"})

    client = TestClient(app)

    response = client.get("/test-error")

    assert response.status_code == 400
    assert response.json() == {
        "error": "example_error",
        "message": "Example message",
        "details": {"field": "value"},
    }


def test_request_validation_errors_with_unpaired_surrogates_return_safe_422() -> None:
    app = create_app()

    @app.get("/test-validation-error")
    def test_validation_error() -> None:
        raise RequestValidationError(
            [
                {
                    "type": "string_unicode",
                    "loc": ("query", "value"),
                    "msg": "Input should be a valid Unicode string",
                    "input": "a\ud800b",
                }
            ]
        )

    response = TestClient(app).get("/test-validation-error")

    assert response.status_code == 422
    assert b'"input":"a\\ud800b"' in response.content


def test_request_body_size_limit_returns_413(monkeypatch) -> None:
    from registry_api import main
    from registry_api.settings import Settings

    monkeypatch.setattr(main, "get_settings", lambda: Settings(max_request_body_bytes=16))
    client = TestClient(main.create_app())

    response = client.post("/contract/validate", content=b"x" * 17, headers={"content-type": "application/json"})

    assert response.status_code == 413
    assert response.json()["error"] == "request_body_too_large"


def test_json_depth_limit_returns_400(monkeypatch) -> None:
    from registry_api import main
    from registry_api.settings import Settings

    monkeypatch.setattr(main, "get_settings", lambda: Settings(max_json_depth=10))
    client = TestClient(main.create_app())
    body = '{"a":' * 11 + "1" + "}" * 11

    response = client.post("/v2/assets", content=body, headers={"content-type": "application/json"})

    assert response.status_code == 400
    assert response.json() == {
        "error": "json_depth_exceeded",
        "message": "JSON request body nesting depth must not exceed 10",
    }


def test_json_depth_limit_ignores_braces_inside_strings(monkeypatch) -> None:
    from registry_api import main
    from registry_api.settings import Settings

    monkeypatch.setattr(main, "get_settings", lambda: Settings(max_json_depth=1))
    client = TestClient(main.create_app())

    response = client.post("/v2/assets", json={"a": "{{{{{{{{{{"})

    assert response.status_code == 422


def test_json_depth_limit_applies_when_content_type_is_missing(monkeypatch) -> None:
    from registry_api import main
    from registry_api.settings import Settings

    monkeypatch.setattr(main, "get_settings", lambda: Settings(max_json_depth=10))
    client = TestClient(main.create_app())
    body = ('{"a":' * 11 + "1" + "}" * 11).encode()

    response = client.post("/v2/assets", content=body)

    assert response.status_code == 400
    assert response.json()["error"] == "json_depth_exceeded"


def test_request_id_header_is_preserved_or_generated() -> None:
    client = TestClient(create_app())

    supplied = client.get("/health", headers={"X-Request-ID": "request-123"})
    generated = client.get("/health")

    assert supplied.headers["X-Request-ID"] == "request-123"
    assert generated.headers["X-Request-ID"]


def test_request_id_header_is_trimmed_and_capped() -> None:
    client = TestClient(create_app())

    response = client.get("/health", headers={"X-Request-ID": f"  {'x' * 200}  "})

    assert response.headers["X-Request-ID"] == "x" * 128


def test_v2_asset_search_openapi_describes_sort_and_category_tag_semantics() -> None:
    client = TestClient(create_app())

    response = client.get("/openapi.json")

    assert response.status_code == 200
    parameters = response.json()["paths"]["/v2/assets"]["get"]["parameters"]
    by_name = {parameter["name"]: parameter for parameter in parameters}
    assert by_name["sort"]["schema"]["enum"] == [
        "asset_id_asc",
        "name_asc",
        "ticker_asc",
        "created_at_desc",
        "updated_at_desc",
    ]
    assert "any of the supplied tags" in by_name["category_tag"]["description"]
