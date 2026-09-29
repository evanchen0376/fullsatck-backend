from pydantic import BaseModel

SUCCESS_CODE = "OK"
SUCCESS_MESSAGE = "success"


class ApiResponse[T](BaseModel):
    """统一响应外壳，与 app.api.exception_handlers 的错误响应结构完全一致。

    必须带类型参数使用，例如 ``ApiResponse[ProductResponse].ok(product)``。
    未参数化时 T 不受约束，data 不做模型转换，ORM 对象会被原样塞进 data
    并在序列化阶段炸掉（且不会报警告）。
    """

    code: str = SUCCESS_CODE
    message: str = SUCCESS_MESSAGE
    data: T | None = None

    @classmethod
    def ok(cls, data: T | None = None, message: str = SUCCESS_MESSAGE) -> "ApiResponse[T]":
        return cls(code=SUCCESS_CODE, message=message, data=data)
