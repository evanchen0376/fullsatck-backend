from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.user import UserRole


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    # 未引入 email-validator，这里只做长度约束；格式校验留给后续按需补充
    email: str = Field(min_length=3, max_length=255)
    # 上限 72 是 bcrypt 的输入长度上限，超过部分会被静默截断，宁可提前拒绝
    password: str = Field(min_length=8, max_length=72)


class UserLogin(BaseModel):
    email: str = Field(min_length=3, max_length=255, description="注册时使用的邮箱")
    # 登录不做强度校验：规则可能随时间变化，旧账号不应因为新规则被锁在门外
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # 秒数，方便前端在到期前主动刷新，而不必解析 JWT 载荷
    expires_in: int


class UserAdminUpdate(BaseModel):
    """管理员改用户角色/启用状态；两个字段都可选，但不能都不传。"""

    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _require_at_least_one(self) -> "UserAdminUpdate":
        if self.role is None and self.is_active is None:
            raise ValueError("At least one of role or is_active is required")
        return self


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime
    # hashed_password 刻意不暴露：响应模型里没有这个字段，ORM 对象序列化时不会泄漏


class UserPage(BaseModel):
    """分页结果：total 为满足条件的总条数，供前端计算总页数。"""

    total: int
    items: list[UserResponse]
