import asyncio
from contextlib import asynccontextmanager


class AsyncBarrier:
    def __init__(self, parties: int):
        self.parties = parties
        self.waiting = 0
        self.event = asyncio.Event()

    async def wait(self):
        self.waiting += 1
        if self.waiting == self.parties:
            self.event.set()
        await self.event.wait()


class SimulatedPool:
    def __init__(self, size: int):
        self._sem = asyncio.Semaphore(size)

    @asynccontextmanager
    async def acquire(self):
        await self._sem.acquire()
        try:
            yield object()
        finally:
            self._sem.release()


async def _hold_background_connection(pool: SimulatedPool, release: asyncio.Event):
    async with pool.acquire():
        await release.wait()


async def _old_request_shape(pool: SimulatedPool, barrier: AsyncBarrier):
    async with pool.acquire():  # DatabaseSessionMiddleware connection
        await barrier.wait()
        async with pool.acquire():  # Old get_db dependency connection
            await asyncio.sleep(0.001)


async def _fixed_request_shape(pool: SimulatedPool, barrier: AsyncBarrier):
    async with pool.acquire():  # DatabaseSessionMiddleware connection reused by get_db
        await barrier.wait()
        await asyncio.sleep(0.001)


async def test_old_request_shape_starves_24_pool_with_12_background_holders():
    pool = SimulatedPool(size=24)
    release_background = asyncio.Event()
    background = [
        asyncio.create_task(_hold_background_connection(pool, release_background))
        for _ in range(12)
    ]
    await asyncio.sleep(0)

    barrier = AsyncBarrier(parties=12)
    requests = [asyncio.create_task(_old_request_shape(pool, barrier)) for _ in range(12)]

    try:
        try:
            await asyncio.wait_for(asyncio.gather(*requests), timeout=0.05)
        except TimeoutError:
            pass
        else:
            raise AssertionError("old request shape unexpectedly completed")
    finally:
        for task in requests:
            task.cancel()
        release_background.set()
        await asyncio.gather(*requests, *background, return_exceptions=True)


async def test_fixed_request_shape_survives_24_pool_with_12_background_holders():
    pool = SimulatedPool(size=24)
    release_background = asyncio.Event()
    background = [
        asyncio.create_task(_hold_background_connection(pool, release_background))
        for _ in range(12)
    ]
    await asyncio.sleep(0)

    barrier = AsyncBarrier(parties=12)
    requests = [asyncio.create_task(_fixed_request_shape(pool, barrier)) for _ in range(12)]

    try:
        await asyncio.wait_for(asyncio.gather(*requests), timeout=0.05)
    finally:
        release_background.set()
        await asyncio.gather(*background, return_exceptions=True)
