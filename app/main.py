from fastapi import FastAPI

from app.api.exception_handlers import app_exception_handler
from app.api.router import api_router
from app.core.exceptions import AppException

app = FastAPI(title="MiniShop")

app.add_exception_handler(AppException, app_exception_handler)
app.include_router(
    api_router,
    prefix="/api/v1",
)

