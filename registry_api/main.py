import json

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from registry_api.api.health import router as health_router
from registry_api.api.legacy import router as legacy_router
from registry_api.api.v2 import router as v2_router
from registry_api.compression import GZipMiddleware
from registry_api.errors import ErrorResponse, RegistryError
from registry_api.observability import RequestLoggingMiddleware, configure_logging
from registry_api.openapi import install_openapi_builder
from registry_api.openapi_metadata import (
    API_DESCRIPTION,
    API_SUMMARY,
    API_VERSION,
    OPENAPI_TAGS,
)
from registry_api.rate_limit import RegistrationRateLimitMiddleware
from registry_api.security import (
    JsonDepthLimitMiddleware,
    RequestBodySizeLimitMiddleware,
)
from registry_api.settings import Settings, get_settings


def create_app(*, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title=settings.app_name,
        summary=API_SUMMARY,
        description=API_DESCRIPTION,
        version=API_VERSION,
        openapi_tags=OPENAPI_TAGS,
        debug=settings.debug,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        GZipMiddleware,
        minimum_size=1000,
        compresslevel=9,
        excluded_content_types={"image/png"},
    )
    app.add_middleware(JsonDepthLimitMiddleware, max_json_depth=settings.max_json_depth)
    app.add_middleware(
        RequestBodySizeLimitMiddleware,
        max_body_bytes=settings.max_request_body_bytes,
    )
    app.add_middleware(
        RegistrationRateLimitMiddleware,
        limit=settings.registration_rate_limit,
        window_seconds=settings.registration_rate_limit_window_seconds,
    )
    app.add_middleware(RequestLoggingMiddleware)

    @app.exception_handler(RegistryError)
    async def registry_error_handler(
        _request: Request, exc: RegistryError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=exc.error, message=exc.message, details=exc.details
            ).model_dump(exclude_none=True),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> Response:
        errors = exc.errors()
        if len(errors) == 1 and errors[0].get("type") == "issuer_key_policy_conflict":
            return JSONResponse(
                status_code=409,
                content=ErrorResponse(
                    error="validation_error",
                    message=errors[0]["msg"],
                ).model_dump(exclude_none=True),
            )
        content = json.dumps(
            {"detail": jsonable_encoder(errors)},
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        return Response(content=content, status_code=422, media_type="application/json")

    app.include_router(health_router)
    app.include_router(legacy_router)
    app.include_router(v2_router)
    install_openapi_builder(app)
    return app


app = create_app()
