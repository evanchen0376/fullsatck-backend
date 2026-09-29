from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.db.session import get_db
from app.schemas.common import ApiResponse

router = APIRouter()

HealthData = dict[str, str]


@router.get("/health", response_model=ApiResponse[HealthData])
async def health() -> ApiResponse[HealthData]:
    return ApiResponse[HealthData].ok({"status": "ok"})


@router.get("/ready", response_model=ApiResponse[HealthData])
async def ready(db: AsyncSession = Depends(get_db)) -> ApiResponse[HealthData]:
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        # 探针必须返回非 2xx，否则 k8s 等编排系统会把不可用的实例当作健康
        raise AppException(
            message="database unavailable",
            code="NOT_READY",
            status_code=503,
        ) from exc

    return ApiResponse[HealthData].ok({"status": "ok"})
