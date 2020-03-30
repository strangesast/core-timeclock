import asyncio
import configparser
from motor.motor_asyncio import AsyncIOMotorDatabase
from pprint import pprint
from datetime import datetime

from util import get_mongo_db
from models import GraphDataResponse

async def get_graph_data(mongo_db: AsyncIOMotorDatabase) -> GraphDataResponse:
    frac = 2 # half hour
    # buckets of seconds
    l = 48 * frac
    buckets = [v * 60 / frac * 60 for v in range(l)]
    buckets = list(zip(buckets[0:-1], buckets[1:]))
    pairs = list(zip(map(str, range(0, l)), map(str, range(int(l/2), l))))
    buckets = [{'$cond': {'if': {'$and': [{'$lte': ['$start', b[0]]}, {'$gt': ['$end', b[1]]}]}, 'then': str(i % int(24 * frac)), 'else': None}}
            for i, b in enumerate(buckets)]
    pipeline = [
        {'$match': {'end': {'$ne': None}}},
        {'$addFields': {
            'start': {'$dateToParts': {'date': '$start', 'timezone': 'America/New_York'}},
            'end': {'$dateToParts': {'date': '$end', 'timezone': 'America/New_York'}},
        }},
        {'$addFields': {
            'start': {'$sum': ['$start.second', {'$multiply': ['$start.minute', 60]}, {'$multiply': ['$start.hour', 3600]}]},
            'end': {'$sum': ['$end.second', {'$multiply': ['$end.minute', 60]}, {'$multiply': ['$end.hour', 3600]}]},
        }},
        {'$addFields': {
            'end': {'$cond': {'if': {'$gte': ['$start', '$end']}, 'then': {'$sum': ['$end', 8.64e4]}, 'else': '$end'}}
        }},
        {'$addFields': {'buckets': buckets}},
        {'$addFields': {'buckets': {'$filter': {'input': '$buckets', 'cond': {'$ne': ['$$this', None]}}}}},
        {'$unwind': '$buckets'},
        {'$facet': {
            'data': [
                {'$group': {'_id': {'employee': '$employee', 'bucket': '$buckets'}, 'count': {'$sum': 1}}},
                {'$group': {
                    '_id': '$_id.bucket',
                    'buckets': {'$push': {'employee': '$_id.employee', 'count': '$count'}},
                    'total': {'$sum': '$count'},
                }},
                {'$sort': {'_id': 1}},
                {'$addFields': {'buckets': {'$map': {'input': '$buckets', 'in': ['$$this.employee', '$$this.count']}}}},
                {'$addFields': {'buckets': {'$arrayToObject': '$buckets'}}},
            ],
            'employees': [
                {'$group': {'_id': '$employee', 'total': {'$sum': 1}}},
                {'$lookup': {'from': 'employees', 'localField': '_id', 'foreignField': 'id', 'as': 'employee'}},
                {'$unwind': '$employee'},
                {'$sort': {'total': -1}},
                {'$replaceRoot': {'newRoot': '$employee'}},
            ],
        }}
    ]
    doc = None
    async for doc in mongo_db.components.aggregate(pipeline):
        break
    if doc is None:
        raise Exception('failed to retrieve graph data')
    employees, data = doc['employees'], doc['data']
    columns = list(map(str, range(24 * frac)))
    return {'columns': columns, 'employees': employees, 'data': data}


async def get_weekly_graph_data(mongo_db: AsyncIOMotorDatabase, _range = None):
    frac = 2 # half hour
    l = 48 * frac
    buckets = [v * 60 / frac * 60 for v in range(l)]
    buckets = list(zip(buckets[0:-1], buckets[1:]))
    pairs = list(zip(map(str, range(0, l)), map(str, range(int(l/2), l))))
    buckets = [{'$cond': {'if': {'$and': [{'$lte': ['$start', b[0]]}, {'$gt': ['$end', b[1]]}]}, 'then': str(i % int(24 * frac)), 'else': None}}
            for i, b in enumerate(buckets)]

    pipeline = [
        {'$match': {'end': {'$ne': None}}},
        {'$addFields': {
            'startDate': '$start',
            'start': {'$dateToParts': {'date': '$start', 'timezone': 'America/New_York'}},
            'end': {'$dateToParts': {'date': '$end', 'timezone': 'America/New_York'}},
        }},
        {'$addFields': {
            'start': {'$sum': ['$start.second', {'$multiply': ['$start.minute', 60]}, {'$multiply': ['$start.hour', 3600]}]},
            'end': {'$sum': ['$end.second', {'$multiply': ['$end.minute', 60]}, {'$multiply': ['$end.hour', 3600]}]},
        }},
        {'$addFields': {
            'end': {'$cond': {'if': {'$gte': ['$start', '$end']}, 'then': {'$sum': ['$end', 8.64e4]}, 'else': '$end'}}
        }},
        {'$addFields': {'buckets': buckets}},
        {'$addFields': {'buckets': {'$filter': {'input': '$buckets', 'cond': {'$ne': ['$$this', None]}}}}},
        {'$unwind': '$buckets'},
        {'$addFields': {
            'startDate': {'$dateToParts': {'date': '$startDate', 'timezone': 'America/New_York'}},
        }},
        {'$addFields': {
            'startDate.day': {'$cond': {'if': {'$gte': ['$buckets', int(l / 2)]}, 'then': {'$add': ['$startDate.day', 1]}, 'else': '$startDate.day'}},
            'buckets': {'$mod': [{'$toInt': '$buckets'}, int(l / 2)]},
        }},
        {'$addFields': {
            'startDate': {'$dateFromParts': {'year': '$startDate.year', 'month': '$startDate.month', 'day': '$startDate.day', 'timezone': 'America/New_York'}}
        }},
        {'$group': {'_id': {'startDate': '$startDate', 'bucket': '$buckets'}, 'total': {'$sum': 1}, 'components': {'$push': {'_id': '$_id', 'employee': '$employee'}}}},
        {'$group': {'_id': '$_id.startDate', 'buckets': {'$push': '$$ROOT'}}},
        {'$addFields': {'buckets': {'$map': {'input': '$buckets', 'in': [{'$toString': '$$this._id.bucket'}, {'total': '$$this.total', 'components': '$$this.components'}]}}}},
        {'$addFields': {'buckets': {'$arrayToObject': '$buckets'}}},
        {'$project': {'date': '$_id', '_id': 0, 'buckets': 1}},
        {'$sort': {'date': 1}},
    ]

    if _range:
        min_date, max_date = _range
        pipeline.insert(0, {'$match': {'$or': [
            {'start': {'$gte': min_date, '$lt': max_date}},
            {'end': {'$gte': min_date, '$lt': max_date}},
            {'start': {'$lte': min_date}, 'end': {'$gt': max_date}},
        ]}})

    return await mongo_db.components.aggregate(pipeline).to_list(None)


async def main():
    config = configparser.ConfigParser()
    config.read('config.ini')
    mongo_client = await get_mongo_db(config['MONGO'])
    mongo_db = mongo_client.timeclock
    res = await get_graph_data(mongo_db)
    pprint(res)
    res = await get_weekly_graph_data(mongo_db)
    pprint(res)
    mongo_client.close()


if __name__ == '__main__':
    asyncio.run(main())
