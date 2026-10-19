from fastapi import APIRouter

from app.api.routers.admin import router as admin_router
from app.api.routers.auth import router as auth_router
from app.api.routers.health import router as health_router
from app.api.routers.products import router as products_router

api_router = APIRouter()

api_router.include_router(
    products_router,
    prefix="/products",
    tags=["products"],
)

api_router.include_router(
    auth_router,
    prefix="/auth",
    tags=["auth"],
)

api_router.include_router(
    admin_router,
    prefix="/admin",
    tags=["admin"],
)

api_router.include_router(
    health_router,
    tags=["health"],
)
