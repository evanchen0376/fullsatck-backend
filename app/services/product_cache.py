"""商品查询缓存（cache-aside）。

为什么放在路由层而不是 ``ProductService``
------------------------------------------
缓存的是**接口契约**（``ProductResponse`` / ``ProductPage``），而 service 返回 ORM 对象；
把 ORM 序列化进缓存再反序列化回 ORM 是绕远路，还要处理 session 身份映射。
写路径的失效逻辑也天然属于「响应组装」这一层——它知道这次写动了哪些返回形态。

两种失效策略并存，是因为代价不同
--------------------------------
- **详情**：按 id 精确 ``DEL``，一次操作搞定。
- **列表**：key 由 ``(version, skip, limit)`` 组成，写操作只 ``INCR`` 一个版本号。
  「这个商品的列表缓存到底有多少个分页 key」是未知数，逐个删就得 ``SCAN`` 全库；
  版本号让旧 key 立刻不可达，再靠 TTL 自然消失，代价恒定 O(1)。

三类缓存问题在这里的处理
------------------------
- **穿透**：不存在的商品也写一个短 TTL 的负缓存标记（``:missing:{id}``），
  否则扫一遍 id 就能把每个请求都打到数据库。因为 ``products.id`` 是自增主键、
  正常不会被复用，负缓存不会把「后来才创建的商品」误判成不存在；
  唯一的例外是 ``TRUNCATE`` / 从备份恢复导致 id 回退，所以创建时也会顺手删掉
  可能存在的旧标记（见 ``invalidate``）。
- **击穿**：不在这一层做，由路由层的 ``SingleFlight`` 把并发回源压成一次。
- **雪崩**：TTL 带 ±10% 抖动，避免同一批 key 同时过期。

一致性边界
----------
cache-aside 的固有窗口：读请求在「miss → 查库」之间如果插入了写操作，
读方可能把旧值写回缓存。详情用的是 ``DEL``，所以只能靠短 TTL（默认 60 秒）兜底；
列表因为带版本号，回填会落到旧版本命名空间上，不会污染新数据。
"""

import logging
import random
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis import delete_by_prefix, get_redis
from app.schemas.product import ProductPage, ProductResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Cached[T]:
    """缓存查询结果。

    ``hit`` 区分「Redis 里没有」和「Redis 里有但值就是空」——后者是负缓存，
    对应 HTTP 404，必须和「未命中、需要查库」区分开，否则负缓存就没意义了。
    """

    hit: bool
    value: T | None = None


class ProductCache:
    def __init__(
        self,
        client: Redis,
        *,
        prefix: str,
        ttl_seconds: int,
        negative_ttl_seconds: int,
        max_cached_offset: int,
    ) -> None:
        self._client = client
        self._prefix = prefix.rstrip(":")
        self._ttl_seconds = ttl_seconds
        self._negative_ttl_seconds = negative_ttl_seconds
        self._max_cached_offset = max_cached_offset

    # ---------- 详情 ----------

    async def get_product(self, product_id: int) -> Cached[ProductResponse]:
        """一次 MGET 同时取「有值」和「不存在」两种标记，避免 miss 时多一次往返。"""
        raw, missing = await self._safe(
            "读取详情",
            self._client.mget([self._detail_key(product_id), self._missing_key(product_id)]),
            [None, None],
        )

        if raw is not None:
            try:
                return Cached(True, ProductResponse.model_validate_json(raw))
            except ValidationError:
                # 结构变更导致旧格式对不上：删掉让它重新从库里取，别让脏数据一直报错
                await self._safe("删除脏详情", self._client.delete(self._detail_key(product_id)))
                return Cached(False)

        if missing is not None:
            return Cached(True, None)

        return Cached(False)

    async def set_product(self, product: ProductResponse) -> None:
        await self._safe(
            "写入详情",
            self._client.set(
                self._detail_key(product.id),
                product.model_dump_json(),
                ex=self._ttl(),
            ),
        )

    async def set_missing(self, product_id: int) -> None:
        """记下「这个 id 确实不存在」，短 TTL 兜住 id 回退等极端情况。"""
        await self._safe(
            "写入空值标记",
            self._client.set(self._missing_key(product_id), "1", ex=self._negative_ttl_seconds),
        )

    # ---------- 列表 ----------

    async def list_version(self) -> int:
        """读列表版本号。读方必须在一次请求内复用同一个版本号（见 set_page 的说明）。"""
        raw = await self._safe("读取列表版本", self._client.get(self._version_key()))
        try:
            return int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            return 0

    async def get_page(self, version: int, skip: int, limit: int) -> ProductPage | None:
        if not self._cacheable(skip, limit):
            return None

        raw = await self._safe(
            "读取列表",
            self._client.get(self._page_key(version, skip, limit)),
        )
        if raw is None:
            return None

        try:
            return ProductPage.model_validate_json(raw)
        except ValidationError:
            await self._safe("删除脏列表", self._client.delete(self._page_key(version, skip, limit)))
            return None

    async def set_page(self, version: int, skip: int, limit: int, page: ProductPage) -> None:
        """``version`` 由调用方传入（而不是在这里重新读一次）。

        如果回填时重新读版本号，可能出现「读到版本 0 → 查库 → 期间写入把版本升到 1
        → 回填时按版本 1 写」，于是旧数据被写进新版本命名空间，缓存里留下的是永久脏值。
        用读方自己看到的版本号回填，最坏情况只是写到一个已经没人读的旧 key 上。
        """
        if not self._cacheable(skip, limit):
            return

        await self._safe(
            "写入列表",
            self._client.set(
                self._page_key(version, skip, limit),
                page.model_dump_json(),
                ex=self._ttl(),
            ),
        )

    # ---------- 失效 ----------

    async def invalidate(self, product_id: int | None = None) -> None:
        """写操作提交后调用。

        ``product_id`` 为 ``None`` 表示不涉及具体商品（只需让列表失效）。
        传入 id 时会同时删详情与负缓存标记：正常自增 id 不会被复用，
        但如果表被 ``TRUNCATE`` 或从旧备份恢复，id 可能回退到曾经被标记为「不存在」的值。
        """
        if product_id is not None:
            await self._safe(
                "删除详情",
                self._client.delete(self._detail_key(product_id), self._missing_key(product_id)),
            )

        # 版本号不设 TTL：它一旦过期归零，之前那些还没到期的旧列表 key 会「复活」
        await self._safe("递增列表版本", self._client.incr(self._version_key()))

    async def reset_all(self) -> int:
        """清空本前缀下的所有 key，仅供测试/手工排障。"""
        try:
            return await delete_by_prefix(self._client, f"{self._prefix}:")
        except (RedisError, OSError) as exc:
            logger.warning("清理商品缓存失败：err=%s", exc)
            return 0

    # ---------- 内部 ----------

    def _detail_key(self, product_id: int) -> str:
        return f"{self._prefix}:detail:{product_id}"

    def _missing_key(self, product_id: int) -> str:
        return f"{self._prefix}:missing:{product_id}"

    def _version_key(self) -> str:
        return f"{self._prefix}:list:version"

    def _page_key(self, version: int, skip: int, limit: int) -> str:
        return f"{self._prefix}:list:v{version}:{skip}:{limit}"

    def _cacheable(self, skip: int, limit: int) -> bool:
        # skip 由调用方随便传：不设上限的话，扫一遍 skip=0..N 就能把 key 空间撑爆
        return skip + limit <= self._max_cached_offset

    def _ttl(self) -> int:
        # ±10% 抖动：避免同一批 key 同时过期，然后在同一瞬间全部打回数据库
        jitter = max(1, self._ttl_seconds // 10)
        return self._ttl_seconds + random.randint(-jitter, jitter)

    async def _safe(self, action: str, awaitable: Any, default: Any = None) -> Any:
        """缓存是旁路：任何 Redis 故障都退化成「当作没命中」，不能影响接口可用性。"""
        try:
            return await awaitable
        except (RedisError, OSError) as exc:
            logger.warning("商品缓存%s失败，回落到数据库：err=%s", action, exc)
            return default


# 全局单例：每次请求 new 一个没有坏处，但复用同一个客户端才能共享连接池
product_cache = ProductCache(
    get_redis(),
    prefix=settings.CACHE_KEY_PREFIX,
    ttl_seconds=settings.PRODUCT_CACHE_TTL_SECONDS,
    negative_ttl_seconds=settings.PRODUCT_CACHE_NEGATIVE_TTL_SECONDS,
    max_cached_offset=settings.PRODUCT_LIST_CACHE_MAX_OFFSET,
)
