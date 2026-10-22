"""登录限流：Redis 滑动窗口（ZSET + Lua 原子判定）。

为什么必须共享状态
------------------
进程内 dict 在 ``--workers N`` / 多副本下各算各的，实际额度变成 N 倍；进程重启即清零；
更麻烦的是体验：同一个用户可能「这次 429、下次 200」，因为请求落到了不同实例。
所以计数必须放在所有实例都能看到的地方——Redis。

数据结构
--------
一个 key 对应一个 ``(IP, 账号)``，value 是 ZSET：

- ``score`` = 该次失败的毫秒时间戳；
- ``member`` = 随机串（同一毫秒内多次失败不会互相覆盖）。

判定时先 ``ZREMRANGEBYSCORE`` 剔除窗口外的记录，再 ``ZCARD`` 数数——这就是滑动窗口，
不会出现固定窗口那种「窗口边界前后各打满一次」的双倍突刺。

原子性
------
「查一下现在几次」和「记一笔失败」分两次调用，但各自都是 Lua 脚本：
Redis 单线程执行脚本，所以每个脚本内部不存在并发交错。
跨进程的 check-then-record 仍有理论上的少量超额，但这与「验密码」天然不可原子有关，
额度只会略微放宽，不会失效。
"""

import logging
import time
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# 判定：剔除窗口外的记录后数数；达到上限则返回还需等待的秒数（0 表示放行）
_CHECK_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_attempts = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
if redis.call('ZCARD', key) < max_attempts then
    return 0
end

-- 最早那次失败滑出窗口时即可重试，所以等的是 oldest + window - now
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local wait_ms = tonumber(oldest[2]) + window - now
local wait_s = math.ceil(wait_ms / 1000)
if wait_s < 1 then
    wait_s = 1
end
return wait_s
"""

# 记录：追加一次失败并续上 TTL。没有 TTL 的 key 会永久占内存，
# 而限流 key 只在「窗口内」有意义，过期即等价于解封。
_RECORD_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local member = ARGV[3]

redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window + 1000)
return redis.call('ZCARD', key)
"""


class RedisLoginRateLimiter:
    """失败计数的滑动窗口。只记失败，登录成功由调用方清零。"""

    def __init__(
        self,
        client: Redis,
        *,
        max_attempts: int,
        window_seconds: int,
        key_prefix: str,
        fail_open: bool = True,
    ) -> None:
        self._client = client
        self._max_attempts = max_attempts
        self._window_ms = int(window_seconds * 1000)
        self._prefix = key_prefix.rstrip(":")
        self._fail_open = fail_open
        # Script 对象只负责 EVALSHA + 退化到 EVAL，不持有业务状态
        self._check = client.register_script(_CHECK_SCRIPT)
        self._record = client.register_script(_RECORD_SCRIPT)

    def _redis_key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    async def retry_after(self, key: str) -> int:
        """返回 0 表示放行；大于 0 表示还需等待的秒数。"""
        try:
            wait = await self._check(
                keys=[self._redis_key(key)],
                args=[self._now_ms(), self._window_ms, self._max_attempts],
            )
        except (RedisError, OSError) as exc:
            # 判定不了就别把登录全锁死：默认放行（可用性优先），审计日志留下痕迹
            return await self._on_check_failure(exc)

        return int(wait or 0)

    async def record_failure(self, key: str) -> None:
        """记一次失败。写失败只记日志：此时登录结果已经定了，不该反过来影响响应。"""
        try:
            await self._record(
                keys=[self._redis_key(key)],
                args=[self._now_ms(), self._window_ms, uuid4().hex],
            )
        except (RedisError, OSError) as exc:
            logger.warning("记录登录失败次数失败（计数会偏松）：key=%s err=%s", key, exc)

    async def reset(self, key: str) -> None:
        """登录成功后清零，避免正常用户被自己之前的手误长期拖累。"""
        try:
            await self._client.delete(self._redis_key(key))
        except (RedisError, OSError) as exc:
            logger.warning("清零登录失败次数失败（用户可能被自己的手误多挡一会儿）：key=%s err=%s", key, exc)

    async def reset_all(self) -> int:
        """只删本前缀的 key。

        用 SCAN 而不是 ``FLUSHDB``：同一台 Redis 可能还有别的环境/别的业务，
        整体清库会误伤。返回删除的 key 数量。
        """
        deleted = 0
        batch: list[str] = []

        try:
            async for found in self._client.scan_iter(match=f"{self._prefix}:*", count=500):
                batch.append(found)
                if len(batch) >= 500:
                    deleted += int(await self._client.delete(*batch))
                    batch.clear()

            if batch:
                deleted += int(await self._client.delete(*batch))
        except (RedisError, OSError) as exc:
            logger.warning("清理限流 key 失败：err=%s", exc)

        return deleted

    async def _on_check_failure(self, exc: Exception) -> int:
        if self._fail_open:
            logger.warning("Redis 限流判定失败，按 fail-open 放行：err=%s", exc)
            return 0

        logger.error("Redis 限流判定失败，按 fail-closed 拒绝：err=%s", exc)
        raise AppException(
            message="Rate limiting is temporarily unavailable, please try again later",
            code="RATE_LIMIT_UNAVAILABLE",
            status_code=503,
            headers={"Retry-After": "30"},
        ) from exc

    @staticmethod
    def _now_ms() -> int:
        # 用本机墙上时钟：多实例之间的时钟偏差通常远小于 900 秒的窗口，
        # 换来的是 Lua 脚本不依赖 Redis 版本（TIME 在旧版本脚本里不可用）
        return int(time.time() * 1000)


# 全局单例：计数必须跨请求共享，放在路由函数里 new 一个就退化成「每次请求都从 0 开始」
login_limiter = RedisLoginRateLimiter(
    get_redis(),
    max_attempts=settings.LOGIN_MAX_ATTEMPTS,
    window_seconds=settings.LOGIN_LOCKOUT_SECONDS,
    key_prefix=settings.RATE_LIMIT_KEY_PREFIX,
    fail_open=settings.RATE_LIMIT_FAIL_OPEN,
)
