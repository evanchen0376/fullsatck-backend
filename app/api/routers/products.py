from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.product import ProductCreate, ProductResponse
from app.services.product_service import ProductService

router = APIRouter()


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_product(
    payload: ProductCreate,
    db: AsyncSession = Depends(get_db),
):
    service = ProductService(db)

    return await service.create_product(payload)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
)

async def get_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    service = ProductService(db)
    return await service.get_product(product_id)

