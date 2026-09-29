from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.product import Product
from app.schemas.product import ProductCreate


class ProductService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_product(
        self,
        payload: ProductCreate,
    ) -> Product:

        product = Product(
            name=payload.name,
            price=payload.price,
            stock=payload.stock,
        )
        self.db.add(product)
        await self.db.commit()
        await self.db.refresh(product)
        return product

    async def get_product(self, product_id: int) -> Product:
        stmt = select(Product).where(Product.id == product_id)
        result = await self.db.execute(stmt)
        product = result.scalars().one_or_none()
        if not product:
            raise AppException(
                message="Product not found",
                code="PRODUCT_NOT_FOUND",
                status_code=404,
            )
        return product

    async def list_products(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Product], int]:
        """返回 (当前页数据, 总条数)，total 供前端计算总页数。"""
        # 必须 order_by：没有确定顺序时 offset/limit 的结果在 SQL 层面不稳定，可能跨页重复或漏数据
        stmt = select(Product).order_by(Product.id).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        total = await self.db.scalar(select(func.count()).select_from(Product)) or 0

        return items, total
