from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.product import ProductCreate, ProductPage, ProductResponse
from app.services.product_service import ProductService

router = APIRouter()


@router.post(
    "",
    response_model=ApiResponse[ProductResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_product(
    payload: ProductCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductResponse]:
    service = ProductService(db)
    product = await service.create_product(payload)

    return ApiResponse[ProductResponse].ok(product)


@router.get(
    "",
    response_model=ApiResponse[ProductPage],
)
async def list_products(
    skip: Annotated[int, Query(ge=0, description="跳过的记录数")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 100,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductPage]:
    service = ProductService(db)
    items, total = await service.list_products(skip=skip, limit=limit)

    return ApiResponse[ProductPage].ok(ProductPage(total=total, items=items))


@router.get(
    "/{product_id}",
    response_model=ApiResponse[ProductResponse],
)
async def get_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductResponse]:
    service = ProductService(db)
    product = await service.get_product(product_id)

    return ApiResponse[ProductResponse].ok(product)
