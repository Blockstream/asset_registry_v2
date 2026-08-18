from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


def install_openapi_builder(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            webhooks=app.webhooks.routes,
            tags=app.openapi_tags,
            servers=app.servers,
            terms_of_service=app.terms_of_service,
            contact=app.contact,
            license_info=app.license_info,
            separate_input_output_schemas=app.separate_input_output_schemas,
        )
        _inline_legacy_contract_request_schemas(schema)
        _close_pattern_property_objects(schema)
        _remove_null_parameter_options(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi


def _inline_legacy_contract_request_schemas(schema: dict[str, Any]) -> None:
    """Keep coverage generators from treating typed legacy extras as known fields.

    Some OpenAPI test-data generators apply a referenced object's
    ``additionalProperties`` schema to its declared properties. Inlining the
    legacy contract at its two request sites preserves the same JSON Schema
    constraints while avoiding invalid values for fields such as
    ``issuer_pubkey``.
    """
    components = schema.get("components", {}).get("schemas", {})
    legacy_contract = components.get("LegacyContractMetadata")
    if not isinstance(legacy_contract, dict):
        return

    for request_schema_name in ("LegacyAssetRequest", "LegacyContractValidationRequest"):
        request_schema = components.get(request_schema_name, {})
        contract_property = request_schema.get("properties", {}).get("contract")
        if not isinstance(contract_property, dict):
            continue
        description = contract_property.get("description")
        contract_property.clear()
        contract_property.update(deepcopy(legacy_contract))
        if description is not None:
            contract_property["description"] = description


def _remove_null_parameter_options(schema: dict[str, Any]) -> None:
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters", []):
                if parameter.get("in") not in {"query", "path", "header", "cookie"}:
                    continue
                _remove_null_schema_option(parameter.get("schema", {}))


def _remove_null_schema_option(schema: dict[str, Any]) -> None:
    options = schema.get("anyOf")
    if not isinstance(options, list):
        return
    non_null_options = [option for option in options if option != {"type": "null"}]
    if len(non_null_options) != 1 or len(non_null_options) == len(options):
        return
    schema.pop("anyOf")
    for key, value in non_null_options[0].items():
        schema.setdefault(key, value)


def _close_pattern_property_objects(value: Any) -> None:
    if isinstance(value, dict):
        if "patternProperties" in value:
            value.setdefault("additionalProperties", False)
        for nested in value.values():
            _close_pattern_property_objects(nested)
    elif isinstance(value, list):
        for nested in value:
            _close_pattern_property_objects(nested)
