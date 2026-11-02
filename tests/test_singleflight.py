"""SingleFlight 单元测试。

它是「缓存击穿」的解法，所以测试要盯住三件事：
同一个 key 必须串行、不同 key 不能被互相拖住、用完的锁必须回收（否则就是内存泄漏）。
"""

import asyncio

import pytest

from app.core.singleflight import SingleFlight

pytestmark = pytest.mark.anyio


async def test_same_key_is_serialized() -> None:
    flight = SingleFlight()
    active = 0
    peak = 0

    async def work() -> None:
        nonlocal active, peak
        async with flight.slot("same"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(work() for _ in range(5)))

    assert peak == 1


async def test_different_keys_do_not_block_each_other() -> None:
    flight = SingleFlight()
    both_inside = asyncio.Event()
    active = 0

    async def work(key: str) -> None:
        nonlocal active
        async with flight.slot(key):
            active += 1
            if active == 2:
                both_inside.set()
            await asyncio.wait_for(both_inside.wait(), timeout=0.5)
            active -= 1

    await asyncio.gather(work("a"), work("b"))


async def test_locks_are_reclaimed() -> None:
    """用完删锁：否则每个出现过的商品 id 都会永久留下一个 Lock 对象。"""
    flight = SingleFlight()

    for key in ("a", "b", "c"):
        async with flight.slot(key):
            pass

    assert flight.size == 0


async def test_exception_still_releases_lock() -> None:
    flight = SingleFlight()

    with pytest.raises(RuntimeError):
        async with flight.slot("boom"):
            raise RuntimeError("boom")

    assert flight.size == 0

    # 还能再次进入：锁没有被泄漏成「永远持有」
    async with flight.slot("boom"):
        pass


async def test_timeout_degrades_instead_of_blocking() -> None:
    """队首卡住时，后来的请求宁可放弃保护直接回源，也不能一直等。"""
    flight = SingleFlight()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def leader() -> None:
        async with flight.slot("hot"):
            entered.set()
            await release.wait()

    async def follower() -> str:
        await entered.wait()
        async with flight.slot("hot", timeout=0.05):
            return "degraded"

    leader_task = asyncio.create_task(leader())
    try:
        assert await follower() == "degraded"
    finally:
        release.set()
        await leader_task

    assert flight.size == 0
