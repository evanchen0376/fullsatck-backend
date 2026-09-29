from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    price: Decimal = Field(gt=0)
    stock: int = Field(ge=0)


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price: Decimal
    stock: int
    created_at: datetime


class ProductPage(BaseModel):
    """分页结果：total 为满足条件的总条数，供前端计算总页数。"""

    total: int
    items: list[ProductResponse]
