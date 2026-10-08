"""商品 CRUD 的接口 + 数据库集成测试。

每个用例都在独立事务中运行（见 conftest.py），因此可以直接断言
「表里到底有几行」，不必担心历史数据干扰。
"""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product

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


def to_decimal(value: object) -> Decimal:
    """响应里的 Decimal 可能被序列化成字符串或数字，统一成 Decimal 再比较。"""
    return Decimal(str(value))


async def count_rows(db_session: AsyncSession) -> int:
    result = await db_session.execute(select(Product.id))
    return len(result.scalars().all())


class TestCreateProduct:
    async def test_returns_201_with_full_entity(self, client: AsyncClient) -> None:
        response = await client.post(PRODUCTS_URL, json=make_payload())

        assert response.status_code == 201
        body = response.json()
        assert body["code"] == "OK"
        assert body["message"] == "success"

        data = body["data"]
        assert data["id"] > 0
        assert data["name"] == "机械键盘"
        assert to_decimal(data["price"]) == Decimal("299.00")
        assert data["stock"] == 10
        assert data["created_at"] is not None

    async def test_persists_row_to_database(self, client: AsyncClient, db_session: AsyncSession) -> None:
        created = await create_product(client, name="静电容键盘")

        # 直接按列查询，绕过 ORM 身份映射，确保真的落到了数据库
        result = await db_session.execute(
            select(Product.name, Product.price, Product.stock).where(Product.id == created["id"])
        )

        assert result.one() == ("静电容键盘", Decimal("299.00"), 10)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("price", "0"),  # gt=0
            ("price", "-1"),
            ("stock", -1),  # ge=0
            ("name", ""),  # min_length=1
            ("name", "x" * 121),  # max_length=120
        ],
    )
    async def test_rejects_invalid_payload(self, client: AsyncClient, field: str, value: object) -> None:
        response = await client.post(PRODUCTS_URL, json=make_payload(**{field: value}))

        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"

    async def test_rejects_missing_required_field(self, client: AsyncClient) -> None:
        response = await client.post(PRODUCTS_URL, json={"name": "缺少价格"})

        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"

    async def test_does_not_persist_when_payload_invalid(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await client.post(PRODUCTS_URL, json=make_payload(price="-5"))

        assert await count_rows(db_session) == 0


class TestGetProduct:
    async def test_returns_entity_by_id(self, client: AsyncClient) -> None:
        created = await create_product(client)

        response = await client.get(f"{PRODUCTS_URL}/{created['id']}")

        assert response.status_code == 200
        assert response.json()["data"] == created

    async def test_returns_unified_404_when_missing(self, client: AsyncClient) -> None:
        response = await client.get(f"{PRODUCTS_URL}/999999")

        assert response.status_code == 404
        assert response.json() == {
            "code": "PRODUCT_NOT_FOUND",
            "message": "Product not found",
            "data": None,
        }

    async def test_rejects_non_integer_id(self, client: AsyncClient) -> None:
        response = await client.get(f"{PRODUCTS_URL}/abc")

        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"


class TestListProducts:
    async def test_returns_empty_page_on_fresh_database(self, client: AsyncClient) -> None:
        response = await client.get(PRODUCTS_URL)

        assert response.status_code == 200
        assert response.json()["data"] == {"total": 0, "items": []}

    async def test_returns_all_items_with_total(self, client: AsyncClient) -> None:
        for i in range(3):
            await create_product(client, name=f"商品{i}")

        page = (await client.get(PRODUCTS_URL)).json()["data"]

        assert page["total"] == 3
        assert [item["name"] for item in page["items"]] == ["商品0", "商品1", "商品2"]

    async def test_paginates_by_skip_and_limit(self, client: AsyncClient) -> None:
        created = [await create_product(client, name=f"商品{i}") for i in range(3)]
        ids = [item["id"] for item in created]

        page = (await client.get(PRODUCTS_URL, params={"skip": 1, "limit": 1})).json()["data"]

        assert page["total"] == 3  # total 是总数而不是当前页条数
        assert [item["id"] for item in page["items"]] == [ids[1]]  # 按 id 升序，分页结果稳定

    @pytest.mark.parametrize(("field", "value"), [("skip", -1), ("limit", 0), ("limit", 101)])
    async def test_rejects_out_of_range_query(self, client: AsyncClient, field: str, value: int) -> None:
        response = await client.get(PRODUCTS_URL, params={field: value})

        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"


class TestUpdateProduct:
    async def test_replaces_all_fields(self, client: AsyncClient) -> None:
        created = await create_product(client)

        response = await client.put(
            f"{PRODUCTS_URL}/{created['id']}",
            json=make_payload(name="人体工学键盘", price="499.50", stock=3),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["id"] == created["id"]
        assert data["name"] == "人体工学键盘"
        assert to_decimal(data["price"]) == Decimal("499.50")
        assert data["stock"] == 3
        assert data["created_at"] == created["created_at"]  # 更新时间不应改动创建时间

    async def test_persists_changes_to_database(self, client: AsyncClient, db_session: AsyncSession) -> None:
        created = await create_product(client)

        await client.put(f"{PRODUCTS_URL}/{created['id']}", json=make_payload(name="改写后的名字", stock=99))

        result = await db_session.execute(select(Product.name, Product.stock).where(Product.id == created["id"]))
        assert result.one() == ("改写后的名字", 99)

    async def test_requires_every_field(self, client: AsyncClient) -> None:
        """PUT 是全量替换：少传字段直接 422，不存在「部分更新」语义。"""
        created = await create_product(client)

        response = await client.put(f"{PRODUCTS_URL}/{created['id']}", json={"name": "只有名字"})

        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"

    async def test_returns_404_when_missing(self, client: AsyncClient) -> None:
        response = await client.put(f"{PRODUCTS_URL}/999999", json=make_payload())

        assert response.status_code == 404
        assert response.json()["code"] == "PRODUCT_NOT_FOUND"

    async def test_does_not_touch_other_rows(self, client: AsyncClient) -> None:
        first = await create_product(client, name="商品A")
        second = await create_product(client, name="商品B")

        await client.put(f"{PRODUCTS_URL}/{second['id']}", json=make_payload(name="商品B改"))

        untouched = (await client.get(f"{PRODUCTS_URL}/{first['id']}")).json()["data"]
        assert untouched["name"] == "商品A"


class TestDeleteProduct:
    async def test_removes_row_from_database(self, client: AsyncClient, db_session: AsyncSession) -> None:
        created = await create_product(client)

        response = await client.delete(f"{PRODUCTS_URL}/{created['id']}")

        assert response.status_code == 200
        assert response.json() == {"code": "OK", "message": "success", "data": None}

        result = await db_session.execute(select(Product.id).where(Product.id == created["id"]))
        assert result.one_or_none() is None

    async def test_returns_404_on_second_delete(self, client: AsyncClient) -> None:
        created = await create_product(client)
        await client.delete(f"{PRODUCTS_URL}/{created['id']}")

        response = await client.delete(f"{PRODUCTS_URL}/{created['id']}")

        assert response.status_code == 404
        assert response.json()["code"] == "PRODUCT_NOT_FOUND"

    async def test_returns_404_when_missing(self, client: AsyncClient) -> None:
        response = await client.delete(f"{PRODUCTS_URL}/999999")

        assert response.status_code == 404
        assert response.json()["code"] == "PRODUCT_NOT_FOUND"


class TestErrorHandling:
    async def test_unknown_route_falls_back_to_unified_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/not-exists")

        assert response.status_code == 404
        assert response.json()["code"] == "HTTP_404"

    async def test_method_not_allowed_is_wrapped(self, client: AsyncClient) -> None:
        response = await client.patch(f"{PRODUCTS_URL}/1", json=make_payload())

        assert response.status_code == 405
        assert response.json()["code"] == "HTTP_405"
