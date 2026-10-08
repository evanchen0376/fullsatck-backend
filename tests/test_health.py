"""健康检查与就绪探针的集成测试。"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import get_db
from app.main import app

pytestmark = pytest.mark.anyio


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "code": "OK",
        "message": "success",
        "data": {"status": "ok"},
    }


async def test_ready_returns_ok_when_database_reachable(client: AsyncClient) -> None:
    """探针真的会执行 SELECT 1，因此这条用例同时验证了测试库可用。"""
    response = await client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok"}


class _BrokenSession:
    """模拟数据库不可用：执行任何语句都抛异常。"""

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("connection refused")


async def test_ready_returns_503_when_database_unavailable() -> None:
    """数据库故障时探针必须返回非 2xx，否则编排系统会把坏实例当健康。"""

    async def broken_get_db() -> AsyncGenerator[_BrokenSession, None]:
        yield _BrokenSession()

    app.dependency_overrides[get_db] = broken_get_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            response = await async_client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json()["code"] == "NOT_READY"
    assert response.json()["message"] == "database unavailable"
