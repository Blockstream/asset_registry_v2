from fastapi.testclient import TestClient

from registry_api.audit import MAX_AUDIT_ID
from registry_api.main import create_app


ASSET_ID = "0" * 64


def _parameter(operation: dict, name: str) -> dict:
    return next(parameter for parameter in operation["parameters"] if parameter["name"] == name)


def test_generated_openapi_constrains_asset_ids_and_documents_custom_errors() -> None:
    schema = create_app().openapi()
    operations = (
        ("/", "post"),
        ("/{asset_id}", "get"),
        ("/{asset_id}", "delete"),
        ("/contract/validate", "post"),
        ("/v2/assets", "post"),
        ("/v2/assets", "get"),
        ("/v2/assets/{asset_id}", "get"),
        ("/v2/assets/{asset_id}/actions", "post"),
        ("/v2/assets/{asset_id}/actions/latest", "get"),
        ("/v2/assets/{asset_id}/migrate", "post"),
        ("/v2/assets/{asset_id}/audit", "get"),
        ("/v2/audit", "get"),
        ("/v2/admin/actions", "post"),
        ("/v2/admin/assets/{asset_id}/actions", "post"),
        ("/v2/admin/assets/{asset_id}/annotations", "put"),
    )

    for path, method in operations:
        assert "400" in schema["paths"][path][method]["responses"]

    for path, method in operations:
        operation = schema["paths"][path][method]
        path_parameters = [parameter for parameter in operation.get("parameters", []) if parameter["in"] == "path"]
        for parameter in path_parameters:
            assert parameter["schema"]["pattern"] == "^[0-9a-fA-F]{64}$"
            assert parameter["schema"]["minLength"] == 64
            assert parameter["schema"]["maxLength"] == 64


def test_generated_openapi_requires_signed_action_bodies() -> None:
    schema = create_app().openapi()
    action_operations = (
        ("/v2/assets/{asset_id}/actions", "post"),
        ("/v2/assets/{asset_id}/migrate", "post"),
        ("/v2/admin/actions", "post"),
        ("/v2/admin/assets/{asset_id}/actions", "post"),
        ("/v2/admin/assets/{asset_id}/annotations", "put"),
    )

    for path, method in action_operations:
        request_body = schema["paths"][path][method]["requestBody"]
        assert request_body["required"] is True
        assert "schema" in request_body["content"]["application/json"]


def test_generated_openapi_does_not_serialize_optional_query_null_as_text() -> None:
    schema = create_app().openapi()

    for path in ("/v2/assets", "/v2/audit"):
        for parameter in schema["paths"][path]["get"]["parameters"]:
            assert {"type": "null"} not in parameter["schema"].get("anyOf", [])


def test_generated_openapi_bounds_audit_ids_to_postgresql_bigint() -> None:
    schema = create_app().openapi()

    for path in ("/v2/assets/{asset_id}/audit", "/v2/audit"):
        parameter = _parameter(schema["paths"][path]["get"], "since_audit_id")
        assert parameter["schema"]["maximum"] == MAX_AUDIT_ID


def test_generated_openapi_exposes_runtime_validation_constraints() -> None:
    schema = create_app().openapi()
    components = schema["components"]["schemas"]

    page = _parameter(schema["paths"]["/v2/assets"]["get"], "page")
    assert page["schema"]["maximum"] == 1_000_000
    assert components["LegacyContractMetadata"]["propertyNames"]["pattern"] == r"^[^\x00]*$"
    assert "[\\x01-\\x08\\x0e-\\x1b!-\\x7f]" in components["ContractMetadata"]["properties"]["name"][
        "pattern"
    ]
    assert components["TradingVenue"]["properties"]["url"]["pattern"].startswith("^[hH][tT][tT][pP]")
    assert components["ReplaceCategoryTagsAction"]["properties"]["category_tags"]["uniqueItems"] is True
    assert components["UpdateAdminPermissionsAction"]["properties"]["permissions"]["uniqueItems"] is True


def test_unknown_and_non_rfc3339_query_values_are_rejected() -> None:
    client = TestClient(create_app())

    unknown = client.get("/v2/audit", params={"x-schemathesis-unknown-property": "42"})
    numeric_datetime = client.get("/v2/audit", params={"to_server_received_at": "0"})

    assert unknown.status_code == 400
    assert unknown.json()["details"] == {"parameters": ["x-schemathesis-unknown-property"]}
    assert numeric_datetime.status_code == 422


def test_cross_field_issuer_key_policy_is_reported_as_a_conflict() -> None:
    response = TestClient(create_app()).post(
        "/v2/assets",
        json={
            "asset_id": ASSET_ID,
            "contract": {
                "entity": {"domain": "example.test"},
                "name": "Example",
                "precision": 0,
                "ticker": "TEST",
                "version": 1,
            },
        },
    )

    assert response.status_code == 409
    assert response.json()["error"] == "validation_error"


def test_reported_invalid_requests_are_rejected_without_server_errors() -> None:
    client = TestClient(create_app())

    assert client.get("/v2/assets/0").status_code == 422
    assert client.get("/v2/assets/0/actions/latest").status_code == 422
    assert client.get("/v2/assets/0/audit").status_code == 422
    assert client.get("/v2/audit", params={"since_audit_id": MAX_AUDIT_ID + 1}).status_code == 422
    assert client.post("/v2/assets/0/migrate").status_code == 422
    assert client.post("/contract/validate", json={"contract": {}, "contract_hash": ASSET_ID}).status_code == 422


def test_legacy_trace_returns_method_not_allowed_for_schema_valid_path() -> None:
    response = TestClient(create_app()).request("TRACE", f"/{ASSET_ID}")

    assert response.status_code == 405
