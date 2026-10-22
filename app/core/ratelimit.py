import asyncio
import time
from collections import deque

from app.core.config import settings


class LoginRateLimiter:
    """进程内滑动窗口计数器（只在登录失败时累加）。

    局限（写在这里避免误用）：
    - 多 worker / 多实例各算各的，绕过成本低；生产要全局一致请换 Redis 的 INCR + EXPIRE。
    - 进程重启即清零。
    - 按 IP + 账号计数，能挡住针对单账号的暴力破解，挡不住分布式撞库。
    """

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def retry_after(self, key: str) -> int:
        """返回 0 表示放行；大于 0 表示还需要等待的秒数。"""
        async with self._lock:
            hits = self._prune(key)
            if not hits:
                self._hits.pop(key, None)
                return 0
            if len(hits) < self.max_attempts:
                return 0
            # 最早一次失败滑出窗口的那一刻就能重试
            return max(1, int(hits[0] + self.window_seconds - time.monotonic()) + 1)

    async def record_failure(self, key: str) -> None:
        async with self._lock:
            self._prune(key).append(time.monotonic())

    async def reset(self, key: str) -> None:
        """登录成功后清零，避免正常用户被自己之前的手误拖住。"""
        async with self._lock:
            self._hits.pop(key, None)

    def _prune(self, key: str) -> deque[float]:
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window_seconds:
            hits.popleft()
        return hits


# 全局单例：计数必须跨请求共享，放在路由函数里 new 一个就失效了
login_limiter = LoginRateLimiter(
    max_attempts=settings.LOGIN_MAX_ATTEMPTS,
    window_seconds=settings.LOGIN_LOCKOUT_SECONDS,
)
