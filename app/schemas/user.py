from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    # 未引入 email-validator，这里只做长度约束；格式校验留给后续按需补充
    email: str = Field(min_length=3, max_length=255)
    # 上限 72 是 bcrypt 的输入长度上限，超过部分会被静默截断，宁可提前拒绝
    password: str = Field(min_length=8, max_length=72)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    is_active: bool
    created_at: datetime
    # hashed_password 刻意不暴露：响应模型里没有这个字段，ORM 对象序列化时不会泄漏
