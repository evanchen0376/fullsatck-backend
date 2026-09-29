from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.exception_handlers import (
    app_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.api.router import api_router
from app.core.exceptions import AppException

app = FastAPI(title="MiniShop")

# 覆盖 FastAPI 默认的 {"detail": ...} 响应体，统一为 {code, message, data}
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

app.include_router(
    api_router,
    prefix="/api/v1",
)
