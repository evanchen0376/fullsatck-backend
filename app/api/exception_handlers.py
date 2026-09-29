from typing import Any

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.utils import is_body_allowed_for_status_code
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppException
from app.schemas.common import ApiResponse


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    data: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """统一错误响应体，字段定义与成功响应共用 ApiResponse，避免两边结构漂移。"""
    payload = ApiResponse[Any](code=code, message=message, data=data).model_dump()

    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content=payload,
    )


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """业务异常：code / message / data 由 AppException 自带。"""
    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        data=exc.data,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """HTTPException：路由未匹配(404)、方法不允许(405)、框架内部抛出的 HTTPException 等。"""
    headers = getattr(exc, "headers", None)

    # 204 / 304 等状态码按 HTTP 规范不允许携带响应体
    if not is_body_allowed_for_status_code(exc.status_code):
        return Response(status_code=exc.status_code, headers=headers)

    return error_response(
        status_code=exc.status_code,
        code=f"HTTP_{exc.status_code}",
        message=str(exc.detail),
        headers=headers,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """请求参数校验失败：完整错误列表放 data，message 只放第一条摘要。"""
    # exc.errors() 的 ctx 里可能带 ValueError 等不可序列化对象，必须过 jsonable_encoder
    errors = jsonable_encoder(exc.errors())

    return error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="VALIDATION_ERROR",
        message=_first_error_message(errors),
        data=errors,
    )


def _first_error_message(errors: list[dict[str, Any]]) -> str:
    """把第一条校验错误拼成可读 message，结构化信息仍保留在 data 中。"""
    if not errors:
        return "Request validation failed"

    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    detail = first.get("msg", "Invalid value")

    return f"{location}: {detail}" if location else str(detail)
