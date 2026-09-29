from typing import Any


class AppException(Exception):
    """业务异常基类，由 app.api.exception_handlers 统一翻译为 {code, message, data} 响应体。"""

    def __init__(
        self,
        message: str,
        code: str,
        status_code: int = 400,
        data: Any = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.data = data
        super().__init__(message)
