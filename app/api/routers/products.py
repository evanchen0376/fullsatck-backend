from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.singleflight import SingleFlight
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.product import (
    ProductCreate,
    ProductPage,
    ProductResponse,
    ProductUpdate,
)
from app.services.product_cache import Cached, product_cache
from app.services.product_service import ProductService, product_not_found

router = APIRouter()

# 击穿保护：热点 key 过期时只放一个请求回源，其余排队等它回填。
# 这把锁只解决「本进程内的并发」，多实例各压一次；跨实例唯一回源需要 Redis 锁，代价更大。
product_single_flight = SingleFlight()


def _detail_response(cached: Cached[ProductResponse]) -> ApiResponse[ProductResponse]:
    """命中负缓存等同于「确定不存在」，与查库得到 404 走同一个错误。"""
    if cached.value is None:
        raise product_not_found()

    return ApiResponse[ProductResponse].ok(cached.value)


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

    # 新增会改变列表 total；同时清掉该 id 可能残留的负缓存标记
    # （自增 id 正常不复用，但 TRUNCATE / 从备份恢复时可能回退到曾经的 id）
    await product_cache.invalidate(product.id)

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

    async with product_single_flight.slot(
        f"products:list:v{version}:{skip}:{limit}",
        timeout=settings.PRODUCT_CACHE_LOCK_TIMEOUT_SECONDS,
    ):
        # 二次检查：排队期间可能已经有别的请求把这一页回填了
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
    if cached.hit:
        return _detail_response(cached)

    async with product_single_flight.slot(
        f"products:detail:{product_id}",
        timeout=settings.PRODUCT_CACHE_LOCK_TIMEOUT_SECONDS,
    ):
        cached = await product_cache.get_product(product_id)
        if cached.hit:
            return _detail_response(cached)

        service = ProductService(db)
        try:
            product = ProductResponse.model_validate(await service.get_product(product_id))
        except AppException as exc:
            # 只有「确实不存在」才写负缓存；其它异常（比如数据库故障）不能记成 404，
            # 否则一次数据库抖动会让一堆正常商品在负缓存 TTL 内变成 404
            if exc.code == "PRODUCT_NOT_FOUND":
                await product_cache.set_missing(product_id)
            raise

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
