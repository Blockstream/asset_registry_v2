from fastapi import APIRouter

from registry_api.settings import get_settings

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Health check", operation_id="healthCheck")
def health_check() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "environment": settings.environment}
