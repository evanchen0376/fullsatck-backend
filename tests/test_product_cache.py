"""商品查询缓存的行为测试。

验证方式说明：要证明「这次读走的是缓存而不是数据库」，最直接的办法是
**绕过接口直接改库**，然后看接口返回的是新值还是旧值——

- 返回旧值 → 命中缓存（数据库里已经是新值了）；
- 返回新值 → 没走缓存。

这比 mock 计数器更可靠：它同时验证了「缓存真的被读到了」和「失效逻辑真的没生效」。
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis
from app.models.product import Product
from app.schemas.product import ProductPage, ProductResponse
from app.services.product_cache import ProductCache

PRODUCTS_URL = "/api/v1/products"

pytestmark = pytest.mark.anyio


def make_payload(**overrides: object) -> dict:
    payload: dict = {"name": "机械键盘", "price": "299.00", "stock": 10}
    payload.update(overrides)
    return payload


async def create_product(client: AsyncClient, **overrides: object) -> dict:
    response = await client.post(PRODUCTS_URL, json=make_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def insert_row_directly(db_session: AsyncSession, name: str) -> None:
    """绕过接口插库：不会触发任何缓存失效，用来制造「库新、缓存旧」的状态。"""
    db_session.add(Product(name=name, price=Decimal("1.00"), stock=1))
    await db_session.flush()


class TestDetailCache:
    async def test_second_read_is_served_from_cache(self, client: AsyncClient, db_session: AsyncSession) -> None:
        created = await create_product(client)
        detail_url = f"{PRODUCTS_URL}/{created['id']}"

        first = await client.get(detail_url)
        assert first.json()["data"]["name"] == "机械键盘"

        # 库里改成「直接改库」，但缓存里还是旧值
        row = await db_session.get(Product, created["id"])
        row.name = "直接改库"
        await db_session.flush()

        second = await client.get(detail_url)
        assert second.json()["data"]["name"] == "机械键盘"

    async def test_entry_carries_ttl(self, client: AsyncClient) -> None:
        created = await create_product(client)
        await client.get(f"{PRODUCTS_URL}/{created['id']}")

        key = f"{settings.CACHE_KEY_PREFIX}:detail:{created['id']}"
        ttl = await get_redis().ttl(key)

        assert 0 < ttl <= settings.PRODUCT_CACHE_TTL_SECONDS * 1.1 + 1

    async def test_update_invalidates_detail(self, client: AsyncClient) -> None:
        created = await create_product(client)
        detail_url = f"{PRODUCTS_URL}/{created['id']}"

        await client.get(detail_url)  # 先填上缓存

        updated = await client.put(detail_url, json=make_payload(name="人体工学键盘"))
        assert updated.status_code == 200

        assert (await client.get(detail_url)).json()["data"]["name"] == "人体工学键盘"

    async def test_delete_invalidates_detail(self, client: AsyncClient) -> None:
        created = await create_product(client)
        detail_url = f"{PRODUCTS_URL}/{created['id']}"

        await client.get(detail_url)  # 先填上缓存
        assert (await client.delete(detail_url)).status_code == 200

        # 缓存没删干净的话这里会返回 200 + 已删除的商品
        response = await client.get(detail_url)
        assert response.status_code == 404
        assert response.json()["code"] == "PRODUCT_NOT_FOUND"

    async def test_missing_product_is_not_negatively_cached(self, client: AsyncClient) -> None:
        response = await client.get(f"{PRODUCTS_URL}/999999")

        assert response.status_code == 404
        assert await get_redis().exists(f"{settings.CACHE_KEY_PREFIX}:detail:999999") == 0


class TestListCache:
    async def test_second_read_is_served_from_cache(self, client: AsyncClient, db_session: AsyncSession) -> None:
        empty = await client.get(PRODUCTS_URL)
        assert empty.json()["data"] == {"total": 0, "items": []}

        await insert_row_directly(db_session, "直插商品")

        # 库里已经有 1 条，但缓存还是「空列表」
        cached = await client.get(PRODUCTS_URL)
        assert cached.json()["data"] == {"total": 0, "items": []}

    async def test_create_invalidates_list(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await client.get(PRODUCTS_URL)  # 缓存 total=0
        await insert_row_directly(db_session, "直插商品")

        # 通过接口新增 → INCR 版本号 → 旧列表缓存不再被读到
        await create_product(client)

        page = (await client.get(PRODUCTS_URL)).json()["data"]
        assert page["total"] == 2
        assert [item["name"] for item in page["items"]] == ["直插商品", "机械键盘"]

    async def test_update_invalidates_list(self, client: AsyncClient) -> None:
        created = await create_product(client, name="旧名字")
        await client.get(PRODUCTS_URL)

        await client.put(f"{PRODUCTS_URL}/{created['id']}", json=make_payload(name="新名字"))

        names = [item["name"] for item in (await client.get(PRODUCTS_URL)).json()["data"]["items"]]
        assert names == ["新名字"]

    async def test_delete_invalidates_list(self, client: AsyncClient) -> None:
        created = await create_product(client)
        await client.get(PRODUCTS_URL)

        await client.delete(f"{PRODUCTS_URL}/{created['id']}")

        page = (await client.get(PRODUCTS_URL)).json()["data"]
        assert page == {"total": 0, "items": []}

    async def test_deep_pagination_is_not_cached(self, client: AsyncClient) -> None:
        """skip 无上限：都缓存的话，扫一遍 skip 就能把 key 空间撑爆。"""
        response = await client.get(PRODUCTS_URL, params={"skip": 2000, "limit": 100})

        assert response.status_code == 200
        page_keys = [key async for key in get_redis().scan_iter(match=f"{settings.CACHE_KEY_PREFIX}:list:v*")]
        assert page_keys == []

    async def test_version_key_is_persistent(self, client: AsyncClient) -> None:
        """版本号一旦过期归零，之前没到期的旧列表 key 会「复活」，所以它不能有 TTL。"""
        await create_product(client)

        assert await get_redis().ttl(f"{settings.CACHE_KEY_PREFIX}:list:version") == -1


class TestCacheDegradation:
    async def test_redis_failure_degrades_to_database(self) -> None:
        """缓存是旁路：Redis 挂了就只能退化成每次查库，不能把接口变成 500。"""
        client = Redis.from_url("redis://127.0.0.1:6390/0", socket_connect_timeout=0.2, socket_timeout=0.2)
        cache = ProductCache(client, prefix="degraded:test", ttl_seconds=10, max_cached_offset=100)
        product = ProductResponse(
            id=1,
            name="离线商品",
            price=Decimal("1.00"),
            stock=1,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )

        try:
            assert await cache.get_product(1) is None
            assert await cache.get_page(0, 0, 10) is None
            # 写与失效都不允许抛：它们在接口的成功路径上
            await cache.set_product(product)
            await cache.set_page(0, 0, 10, ProductPage(total=1, items=[product]))
            await cache.invalidate(1)
            await cache.invalidate()
            assert await cache.reset_all() == 0
        finally:
            await client.aclose()
