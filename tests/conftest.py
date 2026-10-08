"""pytest 全局夹具：真实 MySQL 测试库 + FastAPI 应用集成测试。

隔离策略
--------
每个用例在一条独立的外层事务里跑完：业务代码里的 ``session.commit()``
（``join_transaction_mode="create_savepoint"``）只会释放 SAVEPOINT，
用例结束回滚外层事务，数据既不落库也不互相污染。

测试库连接串来自项目根目录的 ``.env.test`` 的 ``TEST_DATABASE_URL``，
绝不触碰开发库（库名强制以 ``_test`` 结尾，否则直接报错）。
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Product  # noqa: F401  # 导入以让 Base.metadata 感知全部表

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_TEST_FILE = PROJECT_ROOT / ".env.test"


def test_database_url() -> str:
    """读取测试库连接串，并做「别把开发库删了」的安全校验。"""
    load_dotenv(ENV_TEST_FILE)  # 幂等：重复调用不会覆盖已存在的环境变量

    raw = os.getenv("TEST_DATABASE_URL")
    if not raw:
        raise RuntimeError(
            f"缺少 TEST_DATABASE_URL：请在 {ENV_TEST_FILE} 中配置测试库连接串，"
            "例如 mysql+asyncmy://user:pass@127.0.0.1:3306/minishop_test"
        )

    database = make_url(raw).database or ""
    if not database.endswith("_test"):
        raise RuntimeError(f"测试库名必须以 _test 结尾（当前为 {database!r}），拒绝在其上执行建表/删表")

    return raw


async def _reset_schema() -> None:
    """按 ORM 模型重建表结构，保证测试库与模型定义一致。"""
    engine = create_async_engine(test_database_url(), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


async def _drop_schema() -> None:
    engine = create_async_engine(test_database_url(), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """项目只用 asyncio（asyncmy 驱动），显式指定避免 anyio 再去要求安装 trio。"""
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def prepare_database() -> Generator[None, None, None]:
    """整个测试会话建表一次，结束后删表，避免残留脏数据影响下次运行。"""
    asyncio.run(_reset_schema())
    yield
    asyncio.run(_drop_schema())


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """每个用例一个引擎：配合 NullPool，连接不会跨事件循环复用。"""
    engine = create_async_engine(test_database_url(), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """绑定在外层事务上的会话，用例结束整体回滚。"""
    connection = await engine.connect()
    transaction = await connection.begin()

    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,  # 与 app.db.session.SessionLocal 保持一致，避免 commit 后异步惰性加载报错
        join_transaction_mode="create_savepoint",  # 业务层 commit 退化为释放 SAVEPOINT
    )

    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """走 ASGITransport 直连 FastAPI 应用，并把 get_db 换成测试会话。"""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            yield async_client
    finally:
        app.dependency_overrides.pop(get_db, None)
