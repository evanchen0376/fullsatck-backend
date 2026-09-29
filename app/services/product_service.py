from sqlalchemy import select
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
