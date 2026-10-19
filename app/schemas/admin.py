from pydantic import BaseModel


class UserStats(BaseModel):
    total: int
    active: int
    admins: int


class ProductStats(BaseModel):
    total: int
    total_stock: int


class AdminSummary(BaseModel):
    """后台概览：只放聚合数字，不返回明细，避免管理页首屏拖全表。"""

    users: UserStats
    products: ProductStats
