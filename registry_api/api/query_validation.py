from fastapi import Request

from registry_api.errors import ErrorCode, RegistryError


def reject_unknown_query_parameters(request: Request) -> None:
    """Keep runtime query handling aligned with OpenAPI's declared parameters."""
    route = request.scope.get("route")
    dependant = getattr(route, "dependant", None)
    allowed = {field.alias for field in getattr(dependant, "query_params", ())}
    unknown = sorted(set(request.query_params) - allowed)
    if unknown:
        raise RegistryError(
            ErrorCode.VALIDATION_ERROR,
            "unknown query parameter",
            {"parameters": unknown},
        )
