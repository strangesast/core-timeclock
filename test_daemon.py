import asyncio
import asynctest
import time
from contextlib import asynccontextmanager

from daemon import get_connection


@asynccontextmanager
async def db_rollback(conn):
    try:
        await conn.begin()
        yield conn
    finally:
        await conn.rollback()


async def testcase():
    print('waiting')
    await asyncio.sleep(2)
    return 'toast!';


class TestDaemon(asynctest.ClockedTestCase):
    async def test_that_is_true(self):
        task = asyncio.create_task(testcase())
        await self.advance(10)
        val = await task
        self.assertEqual(val, 'toast!')
