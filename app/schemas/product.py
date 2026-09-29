from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    price: Decimal = Field(gt=0)
    stock: int = Field(ge=0)


class ProductUpdate(ProductCreate):
    """PUT 全量替换：字段与创建完全一致、全部必填，单独命名以区分语义。

    与 PATCH 的部分更新不同，PUT 缺少任一字段都会返回 422，传 null 同样不合法，
    不存在「某些字段不修改」这个中间态。
    """


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
