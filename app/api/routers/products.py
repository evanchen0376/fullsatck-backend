from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.product import (
    ProductCreate,
    ProductPage,
    ProductResponse,
    ProductUpdate,
)
from app.services.product_cache import product_cache
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

    # 新增只影响列表（total 变了），详情 key 还不存在
    await product_cache.invalidate()

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
    # 版本号只读一次：回填缓存必须用同一个版本，否则可能把旧数据写进新版本命名空间
    version = await product_cache.list_version()
    cached = await product_cache.get_page(version, skip, limit)
    if cached is not None:
        return ApiResponse[ProductPage].ok(cached)

    service = ProductService(db)
    items, total = await service.list_products(skip=skip, limit=limit)
    page = ProductPage(total=total, items=items)

    await product_cache.set_page(version, skip, limit, page)

    return ApiResponse[ProductPage].ok(page)


@router.get(
    "/{product_id}",
    response_model=ApiResponse[ProductResponse],
)
async def get_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductResponse]:
    cached = await product_cache.get_product(product_id)
    if cached is not None:
        return ApiResponse[ProductResponse].ok(cached)

    service = ProductService(db)
    product = ProductResponse.model_validate(await service.get_product(product_id))

    await product_cache.set_product(product)

    return ApiResponse[ProductResponse].ok(product)


@router.put(
    "/{product_id}",
    response_model=ApiResponse[ProductResponse],
)
async def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductResponse]:
    service = ProductService(db)
    product = await service.update_product(product_id, payload)

    # 先落库再失效：反过来的话，写成功但失效失败会留下永久脏缓存
    await product_cache.invalidate(product_id)

    return ApiResponse[ProductResponse].ok(product)


@router.delete(
    "/{product_id}",
    response_model=ApiResponse[None],
)
async def delete_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[None]:
    service = ProductService(db)
    await service.delete_product(product_id)

    # 详情 key 必须删掉，否则删完还会被缓存「复活」一次
    await product_cache.invalidate(product_id)

    return ApiResponse[None].ok()
