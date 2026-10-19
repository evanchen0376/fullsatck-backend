from typing import Any


class AppException(Exception):
    """业务异常基类，由 app.api.exception_handlers 统一翻译为 {code, message, data} 响应体。"""

    def __init__(
        self,
        message: str,
        code: str,
        status_code: int = 400,
        data: Any = None,
        headers: dict[str, str] | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.data = data
        # 401 需要带 WWW-Authenticate: Bearer，否则不合 RFC 6750，客户端/网关可能不认
        self.headers = headers
        super().__init__(message)
