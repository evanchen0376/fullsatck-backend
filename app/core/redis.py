"""Redis 客户端单例。

只放连接管理，不放业务：限流的算法在 ``app.core.ratelimit``。
``from_url`` 本身不建立连接，第一次发命令才握手，所以模块导入阶段构造是安全的。
"""

from redis.asyncio import Redis

from app.core.config import settings

_client: Redis | None = None


def get_redis() -> Redis:
    """进程内复用同一个客户端（内部自带连接池）；不可跨事件循环复用连接池之外的资源。"""
    global _client
    if _client is None:
        _client = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            # 限流是登录链路上的旁路判定，不能因为 Redis 卡住而拖慢登录
            socket_connect_timeout=settings.REDIS_TIMEOUT_SECONDS,
            socket_timeout=settings.REDIS_TIMEOUT_SECONDS,
            health_check_interval=30,
        )

    return _client


async def close_redis() -> None:
    """应用关闭时释放连接池；不关会在 reload/退出时留下未关闭的 socket。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
