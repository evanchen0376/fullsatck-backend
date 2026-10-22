"""限流器本身的行为测试。

这一层的重点是「换 Redis 到底换来了什么」：多实例共享同一份计数。
所以多数用例直接操作 ``RedisLoginRateLimiter``，而不是绕一圈打 HTTP——
HTTP 路径上的限流行为已经在 ``test_token_lifecycle.py::TestLoginRateLimit`` 覆盖了。
"""

import asyncio
from collections.abc import AsyncGenerator, Callable

import pytest
from redis.asyncio import Redis

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.ratelimit import RedisLoginRateLimiter

pytestmark = pytest.mark.anyio

# 限流器单元用例用自己的前缀，避免和 HTTP 用例互相干扰
TEST_PREFIX = f"{settings.RATE_LIMIT_KEY_PREFIX}:unit"


@pytest.fixture
async def limiter_factory() -> AsyncGenerator[Callable[..., RedisLoginRateLimiter], None]:
    """每个限流器实例配一个独立客户端，模拟「两个进程各连一次 Redis」。"""
    clients: list[Redis] = []

    def build(**overrides: object) -> RedisLoginRateLimiter:
        client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        clients.append(client)
        kwargs: dict[str, object] = {
            "max_attempts": 3,
            "window_seconds": 60,
            "key_prefix": TEST_PREFIX,
            "fail_open": True,
        }
        kwargs.update(overrides)
        return RedisLoginRateLimiter(client, **kwargs)  # type: ignore[arg-type]

    try:
        yield build
    finally:
        for client in clients:
            await client.aclose()


class TestSharedState:
    async def test_counter_is_visible_across_instances(
        self, limiter_factory: Callable[..., RedisLoginRateLimiter]
    ) -> None:
        """核心回归：A 进程记的失败，B 进程必须看得见（这正是内存版做不到的）。"""
        worker_a = limiter_factory()
        worker_b = limiter_factory()
        key = "10.0.0.1:alice@example.com"

        for _ in range(3):
            await worker_a.record_failure(key)

        # B 从来没记过这个 key，但共享 Redis 之后它应该立刻判定超限
        assert await worker_b.retry_after(key) > 0

    async def test_reset_from_another_instance_takes_effect(
        self, limiter_factory: Callable[..., RedisLoginRateLimiter]
    ) -> None:
        worker_a = limiter_factory()
        worker_b = limiter_factory()
        key = "10.0.0.1:bob@example.com"

        for _ in range(3):
            await worker_a.record_failure(key)
        assert await worker_a.retry_after(key) > 0

        # 登录成功发生在 B 上，A 也必须立刻解封
        await worker_b.reset(key)

        assert await worker_a.retry_after(key) == 0

    async def test_keys_are_isolated(self, limiter_factory: Callable[..., RedisLoginRateLimiter]) -> None:
        limiter = limiter_factory()

        for _ in range(3):
            await limiter.record_failure("10.0.0.1:alice@example.com")

        assert await limiter.retry_after("10.0.0.1:alice@example.com") > 0
        # 换账号、换 IP 都不受影响
        assert await limiter.retry_after("10.0.0.1:bob@example.com") == 0
        assert await limiter.retry_after("10.0.0.2:alice@example.com") == 0


class TestSlidingWindow:
    async def test_retry_after_does_not_exceed_window(
        self, limiter_factory: Callable[..., RedisLoginRateLimiter]
    ) -> None:
        limiter = limiter_factory(window_seconds=5)
        key = "10.0.0.1:carol@example.com"

        for _ in range(3):
            await limiter.record_failure(key)

        wait = await limiter.retry_after(key)
        assert 0 < wait <= 5

    async def test_window_slides_after_expiry(self, limiter_factory: Callable[..., RedisLoginRateLimiter]) -> None:
        """窗口外的失败不再算数——这是滑动窗口与「一锁到底」的区别。"""
        limiter = limiter_factory(window_seconds=1, max_attempts=2)
        key = "10.0.0.1:dave@example.com"

        await limiter.record_failure(key)
        await limiter.record_failure(key)
        assert await limiter.retry_after(key) > 0

        await asyncio.sleep(1.1)

        assert await limiter.retry_after(key) == 0

    async def test_failures_in_same_millisecond_all_count(
        self, limiter_factory: Callable[..., RedisLoginRateLimiter]
    ) -> None:
        """ZSET member 必须是随机串：同一毫秒的多次失败若用时间戳当 member 会互相覆盖。"""
        limiter = limiter_factory(max_attempts=3)
        key = "10.0.0.1:eve@example.com"

        await asyncio.gather(*(limiter.record_failure(key) for _ in range(3)))

        assert await limiter.retry_after(key) > 0


class TestRedisFailure:
    async def test_fail_open_allows_login_when_redis_is_down(self) -> None:
        """Redis 不可用时默认放行：限流是旁路，不该让整站登录挂掉。"""
        client = Redis.from_url("redis://127.0.0.1:6390/0", socket_connect_timeout=0.2, socket_timeout=0.2)
        limiter = RedisLoginRateLimiter(
            client,
            max_attempts=3,
            window_seconds=60,
            key_prefix=TEST_PREFIX,
            fail_open=True,
        )

        try:
            assert await limiter.retry_after("10.0.0.1:frank@example.com") == 0
            # 记录失败也不能抛，否则登录成功路径会被 500 打断
            await limiter.record_failure("10.0.0.1:frank@example.com")
            await limiter.reset("10.0.0.1:frank@example.com")
        finally:
            await client.aclose()

    async def test_fail_closed_rejects_when_redis_is_down(self) -> None:
        """安全优先的部署可以反过来选：判定不了就拒绝。"""
        client = Redis.from_url("redis://127.0.0.1:6390/0", socket_connect_timeout=0.2, socket_timeout=0.2)
        limiter = RedisLoginRateLimiter(
            client,
            max_attempts=3,
            window_seconds=60,
            key_prefix=TEST_PREFIX,
            fail_open=False,
        )

        try:
            with pytest.raises(AppException) as exc_info:
                await limiter.retry_after("10.0.0.1:grace@example.com")
        finally:
            await client.aclose()

        assert exc_info.value.code == "RATE_LIMIT_UNAVAILABLE"
        assert exc_info.value.status_code == 503
