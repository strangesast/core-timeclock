# connect to mongo
# provide current / historical object info
# /shifts, /employees
# gunicorn?
import time
import pymongo
import configparser
from aiohttp import web
from datetime import datetime, timedelta
import bson
from bson.json_util import dumps
from aiojobs.aiohttp import setup, spawn

from util import get_mongo_db
from graph import get_graph_data, get_weekly_graph_data


routes = web.RouteTableDef()

async def coro():
    print('sleeping...')
    await asyncio.sleep(2)
    print('done sleeping')


@routes.get('/recheck')
async def recheck(request):
    await spawn(request, coro())
    return web.Response(text='checking')


@routes.get('/data/graph')
async def get_graph(request):
    db = request.app['db'].timeclock;
    res = await get_graph_data(db)
    return web.Response(text=dumps(res))


@routes.get('/data/weekly')
async def get_weekly_graph(request: web.Request):
    q = parse_qs(request.query)
    min_date, max_date = q.get('minDate'), q.get('maxDate')

    _range = [min_date, max_date or (datetime.now() + timedelta(hours=24))] if min_date is not None else None
    db = request.app['db'].timeclock;
    obj = await get_weekly_graph_data(db, _range)
    obj = {'data': obj, 'minDate': min_date, 'maxDate': max_date}

    if 'application/bson' in request.headers.get('accept'):
        resp = web.StreamResponse()
        resp.content_type = 'application/bson'
        await resp.prepare(request) 
        buf = bson.encode(obj)
        resp.content_length = len(buf)
        await resp.write(buf)
    else:
        resp = web.Response(text=dumps(obj))
        resp.content_type = 'text/plain'

    return resp


SHIFTS_PIPELINE = [
    {'$unwind': '$components'},
    {'$lookup': {'from': 'components', 'localField': 'components', 'foreignField': '_id', 'as': 'components'}},
    {'$unwind': {'path': '$components', 'preserveNullAndEmptyArrays': True}},
    {'$addFields': {'components.id': {'$toString': '$components.id'}}},
    {'$addFields': {'components.duration': {'$subtract': [{'$ifNull': ['$components.end', '$$NOW']}, '$components.start']}}},
    {'$group': {'_id': '$_id', 'components': {'$push': '$$ROOT.components'}, 'root': {'$first': '$$ROOT'}}},
    {'$addFields': {'root.components': '$components'}},
    {'$replaceRoot': {'newRoot': '$root'}},
    {'$addFields': {'duration': {'$map': {'input': '$components', 'as': 'comp', 'in': '$$comp.duration'}}}},
    {'$addFields': {'duration': {'$sum': '$duration'}}},
    {'$match': {'row': {'$ne': [None]}}},
    {'$addFields': {
        'id': {'$toString': '$_id'},
        'expectedDuration': 1000 * 60 * 60 * 8,
    }},
    {'$project': {'_id': 0}},
]

@routes.get('/data/shifts')
async def get_shifts(request):
    q = parse_qs(request.query)

    min_date, max_date = q.get('minDate'), q.get('maxDate')
    limit = 2000

    db = request.app['db'].timeclock;

    if max_date is None and min_date is None:
        query = {}
    elif min_date is None:
        query = {'start': {'$lt': max_date}}
    elif max_date is None:
        query = {'$or': [{'start': {'$gt': min_date}, 'end': None}, {'end': {'$gt': min_date}}]}
    else: 
        query = {
            '$or': [
                {'start': {'$gt': min_date, '$lt': max_date}},
                {'$and': [{'end': None}, {'start': {'$lt': max_date}}]},
                {'end': {'$gt': min_date, '$lt': max_date}},
                {'$and': [{'start': {'$lt': min_date}}, {'end': {'$gt': max_date}}]},
            ],
        }

    # uggglyy
    #query['flagged'] = False

    if employee_id := q.get('employee'):
        employee = await db.employees.find_one({'id': employee_id}, projection={'_id': False})
        if employee is None:
            return web.HTTPNotFound(body=f'no employee with id: "{employee_id}"')
        query['employee'] = employee_id
        employees = {employee_id: employee}
    else:
        employees = await db.employees.find({}, projection={'_id': False}).to_list(100)
        employees = {id: employee for employee in employees if (id := employee.get('id'))}

    # ayy join


    pipeline = [{'$match': query}, *SHIFTS_PIPELINE]

    shifts = await db.shifts.aggregate(pipeline).to_list(None)

    obj = {
        'employees': employees,
        'employeeIds': list(employees.keys()),
        'shifts': shifts,
    }

    if 'application/bson' in request.headers.get('accept'):
        resp = web.StreamResponse()
        resp.content_type = 'application/bson'
        await resp.prepare(request) 
        buf = bson.encode(obj)
        resp.content_length = len(buf)
        await resp.write(buf)
    else:
        resp = web.Response(text=dumps(obj))
        resp.content_type = 'text/plain'

    return resp


def parse_qs(query):
    result = {}
    for key in ['minDate', 'maxDate']:
        if key in query:
            try:
                result[key] = datetime.fromisoformat(query[key].replace('Z', '+00:00'))
            except ValueError:
                pass
    if 'employee' in query:
        try:
            int(query['employee'])
            result['employee'] = query['employee']
        except ValueError:
            pass

    return result


async def main():
    config = configparser.ConfigParser()
    config.read('config.ini')
    app = web.Application()
    app.add_routes(routes)
    app['db'] = await get_mongo_db(config['MONGO'])
    setup(app)
    return app


if __name__ == '__main__':
    web.run_app(main(), host='0.0.0.0', port=8081)
