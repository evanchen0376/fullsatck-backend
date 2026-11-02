"""进程内的「单飞」：把同一个 key 的并发回源压成一次。

典型场景是缓存击穿——热点 key 刚过期，几十个请求同时 miss，然后一起去查库。
排队等第一个请求回填完，其余请求直接读缓存即可。

为什么只做进程内
----------------
跨实例需要 Redis 分布式锁：要处理锁超时、持有者宕机、误删别人的锁，故障面比
「并发回源几次」大得多。这里取「能用且不引入新故障点」的那一档——回源次数从
「并发数」降到「实例数」，多实例时每个实例各回源一次，仍能接受。
等真正需要全局唯一回源时，再换 Redis 锁，接口不用变。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class SingleFlight:
    def __init__(self) -> None:
        # key → 当前用于串行化的锁
        self._locks: dict[str, asyncio.Lock] = {}
        # key → 正在使用（持有 + 排队）它的协程数，用来判断何时可以回收锁
        self._waiters: dict[str, int] = {}
        # 保护上面两个字典本身：await 之间会切换协程，读-改-写必须互斥
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def slot(self, key: str, *, timeout: float | None = None) -> AsyncIterator[None]:
        """进入 key 的临界区；同一 key 同一时刻只有一个协程在里面。

        ``timeout`` 是**等锁**的上限：拿到锁的请求如果卡住（比如数据库慢查询），
        后面的请求不该无限排队——超时后退化成「没有保护」直接回源，
        宁可多查几次库，也不要让请求挂死在这里。
        """
        lock = await self._register(key)
        acquired = False

        try:
            if timeout is None:
                await lock.acquire()
                acquired = True
            else:
                try:
                    async with asyncio.timeout(timeout):
                        await lock.acquire()
                    acquired = True
                except TimeoutError:
                    logger.warning("等待 %s 的缓存重建超时，改为直接回源", key)

            yield
        finally:
            if acquired:
                lock.release()
            await self._unregister(key)

    @property
    def size(self) -> int:
        """当前跟踪的 key 数量，用于测试断言「锁没有泄漏」。"""
        return len(self._locks)

    async def _register(self, key: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = asyncio.Lock()
            self._waiters[key] = self._waiters.get(key, 0) + 1
            return lock

    async def _unregister(self, key: str) -> None:
        async with self._guard:
            remaining = self._waiters.get(key, 1) - 1
            if remaining > 0:
                self._waiters[key] = remaining
                return

            # 最后一个使用者离开：连同锁一起删掉。
            # 不删的话，每出现过一个不同的商品 id 就会永久留下一个 Lock 对象（慢速泄漏）。
            self._waiters.pop(key, None)
            self._locks.pop(key, None)
