# connect to mongo
# tail cursor on 'state' collection
# accept websocket connections at /socket
# update clients as required
# log?
import asyncio
import pymongo
import configparser
from weakref import WeakSet
from aiohttp import web, WSCloseCode
from pymongo.cursor import CursorType
from bson.json_util import dumps

from util import get_mongo_db


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    request.app['websockets'].add(ws)
    db = request.app['db'].timeclock
    latest_state = await db.state.find_one({}, sort=[('_id', pymongo.DESCENDING)])
    await ws.send_str(dumps(latest_state))
    try:
        async for msg in ws:
            pass
    finally:
        request.app['websockets'].discard(ws)

    return ws


async def background(app):
    try:
        db = app['db'].timeclock;
        websockets = app['websockets']

        while True:
            latest_state = await db.state.find_one({}, sort=[('_id', pymongo.DESCENDING)])
            query = {'_id': {'$gt': latest_state['_id']}} if latest_state else {}
            cursor = db.state.find(query, cursor_type=CursorType.TAILABLE_AWAIT)
            while True:
                if not cursor.alive:
                    await asyncio.sleep(1)
                    break

                #await asyncio.sleep(1)
                #i += 1
                #print(f'{i=}, {len(websockets)=}')
                #await asyncio.gather(*[ws.send_str(dumps(str(i))) for ws in websockets.copy()])

                async for latest_state in cursor:
                    for ws in websockets:
                        await ws.send_str(dumps(latest_state))
                
    except asyncio.CancelledError:
        await asyncio.gather(*[ws.close(code=WSCloseCode.GOING_AWAY, message='Server shutdown') for ws in websockets])

    finally:
        pass


async def start_background_tasks(app):
    app['background'] = asyncio.create_task(background(app))


async def cleanup_background_tasks(app):
    app['background'].cancel()
    await app['background']


async def main():
    config = configparser.ConfigParser()
    config.read('config.ini')
    app = web.Application()
    app['db'] = await get_mongo_db(config['MONGO'])
    app['websockets'] = WeakSet()
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    app.add_routes([web.get('/socket', websocket_handler)])
    return app


if __name__ == '__main__':
    web.run_app(main(), host='0.0.0.0', port=8082)
