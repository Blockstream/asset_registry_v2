from typing import Any

from pydantic import BaseModel, ConfigDict

from registry_api.errors import ErrorResponse


class FrameworkErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str


ASSET_CACHE_HEADERS = {
    "Last-Modified": {
        "description": "Asset modification time in HTTP-date format, when available.",
        "schema": {"type": "string"},
    },
    "Cache-Control": {
        "description": "Requires revalidation before reusing the cached response.",
        "schema": {"type": "string", "enum": ["no-cache"]},
    },
    "Vary": {"schema": {"type": "string"}},
}
ASSET_CACHE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"headers": ASSET_CACHE_HEADERS},
    304: {
        "description": "Assets have not changed since If-Modified-Since; no response body.",
        "headers": ASSET_CACHE_HEADERS,
    },
}


STANDARD_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "model": ErrorResponse,
        "description": "Malformed or semantically invalid request.",
    },
    401: {
        "model": ErrorResponse,
        "description": "Authentication or signature verification failed.",
    },
    403: {
        "model": ErrorResponse,
        "description": "The authenticated actor is not authorized.",
    },
    404: {
        "model": ErrorResponse | FrameworkErrorResponse,
        "description": "The requested resource or route was not found.",
    },
    409: {
        "model": ErrorResponse,
        "description": "The request conflicts with current registry state.",
    },
    413: {
        "model": ErrorResponse,
        "description": "The request body exceeds the configured size limit.",
    },
    503: {
        "model": ErrorResponse,
        "description": "A required verification service is unavailable.",
    },
}

RATE_LIMIT_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    429: {
        "model": ErrorResponse,
        "description": "The request exceeded a registration or domain-proof fetch limit.",
    }
}
