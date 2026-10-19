from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.user import User, UserRole
from app.schemas.admin import AdminSummary, ProductStats, UserStats


class AdminService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_summary(self) -> AdminSummary:
        """后台概览：用户与商品的关键计数。

        五次聚合查询，没有 JOIN —— 各表统计互不影响，JOIN 反而会因笛卡尔积放大结果。
        数据量上来后这里是天然的缓存点（Redis + 短 TTL）。
        """
        user_total = await self.db.scalar(select(func.count()).select_from(User)) or 0
        user_active = await self.db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
        user_admins = (
            await self.db.scalar(select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)) or 0
        )

        product_total = await self.db.scalar(select(func.count()).select_from(Product)) or 0
        # 没有商品时 SUM 返回 NULL，用 coalesce 兜成 0，否则响应里会出现 null
        product_stock = await self.db.scalar(select(func.coalesce(func.sum(Product.stock), 0))) or 0

        return AdminSummary(
            users=UserStats(total=user_total, active=user_active, admins=user_admins),
            products=ProductStats(total=product_total, total_stock=int(product_stock)),
        )
