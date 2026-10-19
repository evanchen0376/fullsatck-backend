from fastapi import APIRouter

from app.api.routers.health import router as health_router
from app.api.routers.products import router as products_router
from app.api.routers.users import router as users_router

api_router = APIRouter()

api_router.include_router(
    products_router,
    prefix="/products",
    tags=["products"],
)

api_router.include_router(
    users_router,
    prefix="/users",
    tags=["users"],
)

api_router.include_router(
    health_router,
    tags=["health"],
)
