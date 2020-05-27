# connect to AMG server / mysql
# poll on interval / based on device schedule
# send updates to mongo, tailable collection

# init.py
import os
import sys
import pytz
import pymongo
import logging
import asyncio
import aiomysql
import configparser
from typing import List
from datetime import datetime, timedelta
from pymongo.errors import BulkWriteError
from pymongo import ReplaceOne
from bson.codec_options import CodecOptions, TypeRegistry

import models
from util import get_async_rpc_connection, get_mysql_db, get_mongo_db, EmployeeShiftColor
from calculate_rows import recalculate

tz = pytz.timezone('US/Eastern')


async def init(mongo_db, mysql_db):
    col_names = await mongo_db.list_collection_names()

    mysql_cursor = await mysql_db.cursor(aiomysql.DictCursor)
    ops = []

    await mysql_cursor.execute('select id,Code,Name,MiddleName,LastName,HireDate from tam.inf_employee')
    async for employee in mysql_cursor:
        employee_id = employee['id']
        color = models.EmployeeShiftColor(employee_id % len(models.EmployeeShiftColor))
        employee_id = str(employee_id)
        employee['id'] = employee_id
        employee['Color'] = color
        ops.append(ReplaceOne({'id': employee_id}, employee, upsert=True))

    await mongo_db.employees.create_index('id', unique=True)
    await mongo_db.employees.bulk_write(ops)

    if 'polls' not in col_names:
        await mongo_db.create_collection('polls');
        await mongo_db.polls.create_index('date', unique=True)

    if 'state' not in col_names:
        await mongo_db.create_collection('state', capped=True, size=100000)

    # need to replace this to calculate duration on the fly
    #await mongo_db.command({
    #    'create': 'shifts',
    #    'viewOn': 'components',
    #    'pipeline': [
    #        {
    #            '$sort': { 'employee': 1, 'start': 1 }
    #        },
    #        {
    #            '$match': { 'start': { '$ne': None }, }
    #        },
    #        {
    #            '$addFields': {
    #                #'end': { '$ifNull': [ '$end', '$$NOW' ] },
    #                'state': {'$cond': [
    #                    {'$ne': ['$end', None]},
    #                    'complete',
    #                    'incomplete'
    #                ]}}
    #        },
    #        {
    #            '$addFields': { 'duration': { '$subtract': [ '$end', '$start' ] } }
    #        },
    #        {
    #            '$group': {
    #                '_id': { 'date': '$date', 'employee': '$employee' }, 
    #                'root': { '$first': '$$ROOT' }, 
    #                #'start': { '$first': '$start' }, 
    #                #'end': { '$last': '$end' }, 
    #                'duration': { '$sum': '$duration' }, 
    #                'components': { '$push': { 'start': '$start', 'end': '$end', 'duration': '$duration' } }
    #            }
    #        },
    #        {
    #            '$addFields': {
    #                'root.components': '$components', 
    #                'root.duration': { '$divide': [ '$root.duration', 3600000 ] }, 
    #                'root.start': '$start', 
    #                'root.end': '$end'
    #            }
    #        },
    #        {
    #            '$replaceRoot': { 'newRoot': '$root' }
    #        },
    #        #{
    #        #    '$match': { 'duration': { '$lt': 24 } }
    #        #}
    #    ]})
    await mongo_db.components.create_index('start')
    await mongo_db.components.create_index('end')
    await mongo_db.components.create_index('employee')


async def main(config):
    # do some init stuff
    # on interval, recheck

    mysql_client = await get_mysql_db(config['MYSQL'])
    mongo_client = await get_mongo_db(config['MONGO'])

    amg_rpc_proxy = get_async_rpc_connection(config['AMG'])

    mongo_db = mongo_client.timeclock
    logging.info('running init')
    await init(mongo_db, mysql_client)
    mysql_client.close()

    interval = timedelta(hours=1)
    buf = 60 # 1 minute. added to interval, or used as timeout between retries

    try:
        # update polls, wait for next poll update after interval
        latest_poll = d.get('date') if (d := await mongo_db.polls.find_one({}, sort=[('date', pymongo.DESCENDING)])) else None
        latest_sync = d.get('date') if (d := await mongo_db.sync_history.find_one({}, sort=[('date', pymongo.DESCENDING)])) else None

        while True:

            mysql_client = await get_mysql_db(config['MYSQL'])
            async with mysql_client.cursor() as mysql_cursor:
                if latest_poll:
                    await mysql_cursor.execute('select StartTime from tam.polllog where StartTime > %s order by StartTime desc',
                            (latest_poll + tz.utcoffset(latest_poll),))
                else:
                    await mysql_cursor.execute('select StartTime from tam.polllog order by StartTime desc')
                    
                if mysql_cursor.rowcount:
                    polls = [{'date': tz.localize(date).astimezone(pytz.UTC).replace(tzinfo=None)} for date, in
                            await mysql_cursor.fetchall()]
                    print(f'{len(polls)=}')
                    latest_poll = polls[0]['date']
                    await mongo_db.polls.insert_many(polls)
            mysql_client.close()


            now = datetime.utcnow()

            logging.info(f'{now=}')
            logging.info(f'{latest_poll=}')
            logging.info(f'{latest_sync=}')

            if latest_poll and latest_sync and latest_sync > latest_poll:
                timeout_duration = d if (d := (latest_poll + interval - now).total_seconds()) > 0 else 60
                logging.info(f'sleeping for {timeout_duration} seconds')
                await asyncio.sleep(timeout_duration)
                continue

            min_date = get_sunday((min(now, latest_sync) if latest_sync else (now - timedelta(days=365))).astimezone(tz)).replace(tzinfo=None)
            #min_date = min(get_sunday(now.astimezone(tz)), get_sunday(latest_sync)) if latest_sync else get_sunday(now - timedelta(days=365))
            logging.info(f'{min_date=}')

            await update(mongo_db, amg_rpc_proxy, min_date, now)
            await recalculate(mongo_db, min_date)
            print(f'inserting... {now}')
            await mongo_db.sync_history.insert_one({'date': now})
            latest_sync = now

    except asyncio.CancelledError:
        pass

    finally:
        logging.info('cancelled')
        await amg_rpc_proxy.close()
        #mysql_client.close()
        mongo_client.close()


async def update(mongo_db, proxy, min_date: datetime, now: datetime):
    # useful if encoding strange types
    type_registry = TypeRegistry(fallback_encoder=timedelta_encoder)
    codec_options = CodecOptions(type_registry=type_registry)
    shifts_col = mongo_db.get_collection('shifts', codec_options=codec_options)

    employee_ids = [int(empl['id']) for empl in await mongo_db.employees.find({}).to_list(None)]

    logging.info('update')
    interval = timedelta(days=14)

    while min_date < now:
        max_date = min_date + interval
        logging.info(f'{min_date} - {max_date}')
    
        employee_timecards = await proxy.GetTimecards(employee_ids, min_date, max_date, False)
    
        count = 0
        for employee_id, components in parse_timecard(employee_timecards):
            for component in components:
                start, end = component['start'], component['end']

                if start is None:
                    continue

                # existing component, perhaps it has been finished
                existing_component = await mongo_db.components.find_one({'employee': employee_id, 'start': start})
                
                if existing_component is not None:
                    component_id = existing_component['_id']
                    await mongo_db.components.update_one({'_id': component_id}, {'$set': component})

                    parent_shift = await mongo_db.shifts.find_one({'components': component_id});
                    if parent_shift is None:
                        raise Exception('missing parent_shift for component')

                    peer_components = await mongo_db.components.find({'_id': {'$in': parent_shift['components']}}).sort([('start', pymongo.ASCENDING)]).to_list(None);

                    if len(peer_components) != len(parent_shift['components']):
                        raise Exception('missing component for parent_shift')

                    start = peer_components[0]['start']
                    end = peer_components[-1]['end']
                    duration = get_duration(peer_components)
                    shift_state = models.ShiftState.Incomplete if end is None else models.ShiftState.Complete
                    await shifts_col.find_one_and_update({'_id': parent_shift['_id']},
                            {'$set': {'start': start, 'end': end, 'duration': duration, 'state': shift_state}})
                else:
                    result = await mongo_db.components.insert_one(component)
                    component_id = result.inserted_id

                    parent_shift = await shifts_col.find_one({
                        'employee': employee_id,
                        'end': {'$lte': start, '$gt': start - timedelta(hours=4)}
                        }, sort=[('end', -1)])

                    shift_state = models.ShiftState.Incomplete if end is None else models.ShiftState.Complete

                    if parent_shift is None:
                        duration = end - start if end is not None else timedelta()
                        result = await shifts_col.insert_one({'employee': employee_id,
                            'components': [component_id], 'start': start, 'end': end,
                            'duration': duration, 'state': shift_state })
                    else:
                        shift_id = parent_shift['_id']

                        peer_components = await mongo_db.components.find({'_id': {'$in': parent_shift['components']}}).sort([('start', pymongo.ASCENDING)]).to_list(None);
    
                        if len(peer_components) != len(parent_shift['components']):
                            raise Exception('missing component for parent_shift')

                        peer_components.append(component)
                        peer_components.sort(key=lambda c: c['start'])
    
                        start = peer_components[0]['start']
                        end = peer_components[-1]['end']
                        duration = get_duration(peer_components)

                        await shifts_col.update_one({'_id': shift_id}, {
                            '$push': {'components': component_id},
                            '$set': {
                                'end': end,
                                'start': start,
                                'state': shift_state,
                                'duration': duration,
                            }})
                count += 1
        logging.info(f'{count=}')
        min_date = max_date

    values = []
    next_state = {}
    current_state = await mongo_db.state.find_one({}, sort=[('date', pymongo.DESCENDING)])

    value_ids = set()
    async for value in mongo_db.shifts.aggregate([
        {'$match': {'state': 'incomplete'}},
        {'$sort': {'start': -1}},
        {'$group': {'_id': '$employee', 'value': {'$first': '$$ROOT'}}},
        {'$replaceRoot': {'newRoot': '$value'}},
        {'$sort': {'start': -1}},
        ]):
        value_ids.add(value['_id'])
        values.append(value)

    async for value in mongo_db.state.aggregate([
        {'$sort': {'date': -1}},
        {'$limit': 1},
        {'$unwind': '$values'},
        {'$match': {'$expr': {'$eq': ['$values.end', None]}}}, # remove shifts that have ended
        {'$lookup': {'from': 'shifts', 'localField': 'values._id', 'foreignField': '_id', 'as': 'nextValues'}},
        {'$unwind': '$nextValues'},
        {'$replaceRoot': {'newRoot': '$nextValues'}},
        ]):
        if value['_id'] not in value_ids:
            values.append(value)

    await mongo_db.state.insert_one({'date': now, 'values': values})


async def update_shifts(proxy, employee_ids, min_date, end_date = datetime.now(), interval = timedelta(days=14)):
    while min_date < end_date:
        max_date = min_date + interval
        logging.info(f'{min_date} - {max_date}')

        employee_timecards = await proxy.GetTimecards(employee_ids, min_date, max_date, False)
        now = datetime.now()
        group = [item for tc in employee_timecards for item in parse_timecard(tc)]

        yield group, max_date
        min_date = max_date


def parse_timecard(timecards):
    for timecard in timecards:
        employee_id, items = str(timecard['EmployeeId']), timecard['Timecards']
        def it():
            for item in items:
                punches = []
                obj = {'punches': punches, 'employee': employee_id}
                for k0, k1 in [('date', 'Date'), ('isManual', 'IsManual'), ('hours', 'Reg')]:
                    obj[k0] = item.get(k1)
                for k0, k1 in [('start', 'StartPunch'), ('end', 'StopPunch')]:
                    if (p := item.get(k1)):
                        obj[k0] = tz.localize(p['OriginalDate']).astimezone(pytz.UTC).replace(tzinfo=None)
                        punches.append(p['Id'])
                    else:
                        obj[k0] = None
                yield obj
        yield employee_id, it()


async def update_shift_stats(db):
    pipeline = [
      {'$match': {'end': {'$ne': None}}},
      {'$addFields': {
          'startParts': {'$dateToParts': {'date': '$start'}},
          'endParts': {'$dateToParts': {'date': '$end'}}
      }},
      {'$addFields': {
          'startSecs': {
              '$add': [
                  '$startParts.millisecond',
                  {'$multiply': ['$startParts.second', 1000]},
                  {'$multiply': ['$startParts.minute', 60000]},
                  {'$multiply': ['$startParts.hour', 60*60*1000]}
              ]},
          'endSecs': {
              '$add': [
                  '$endParts.millisecond',
                  {'$multiply': ['$endParts.second', 1000]},
                  {'$multiply': ['$endParts.minute', 60000]},
                  {'$multiply': ['$endParts.hour', 60*60*1000]}
              ]}
      }},
      {'$addFields': {
          # compensate for overnight shifts
          'endSecs': {'$cond': {
              'if': { '$gte': [ '$startSecs', '$endSecs' ] },
              'then': {'$add': ['$endSecs', 24*60*60*1000]},
              'else': '$endSecs'
              }},
          'duration': {'$divide': ['$duration', 3.6e6]}
      }},
      {'$group': {
          '_id': '$employee',
          'start': {'$avg': '$startSecs'},
          'end': {'$avg': '$endSecs'},
          'duration': {'$avg': '$duration'},
          'stdDev': {'$stdDevPop': '$duration'},
          'count': {'$sum': 1},
          'asOf': {'$max': '$end'},
      }},
      {'$addFields': {
          'count': {'$toInt': '$count'},
          'start': {'$dateFromParts': {'year': 2000, 'month': 1, 'day': 1, 'millisecond': {'$toInt': '$start'}}},
          'end': {'$dateFromParts': {'year': 2000, 'month': 1, 'day': 1, 'millisecond': {'$toInt': '$end'}}},
          'calculatedOn': now,
      }},
      {'$project': {'stats': '$$ROOT'}},
      {'$project': {'stats': 1, 'id': '$_id', '_id': 0}},
      # add 'stats' property to employee docs
      {'$merge': {'into': 'employees', 'on': 'id'}}
    ]

    # add stats for each employee
    async for doc in db.shifts.aggregate(pipeline):
        pass


def get_duration(components: List[models.ShiftComponent]) -> timedelta:
    '''
    if all start & end not None, add deltas up
    '''
    duration = timedelta()
    if all(arr := [t for c in components for t in (c['start'], c['end'])]):
        for a, b in zip(arr[0::2], arr[1::2]):
            duration += b - a
    return duration


def timedelta_encoder(value):
    if isinstance(value, timedelta):
        return int(value.total_seconds() * 1000)
    if isinstance(value, Enum):
        return value.value
    return value


def get_sunday(dt: datetime):
    dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    dt = dt - timedelta(days=(dt.weekday() + 1) % 7)
    return dt


if __name__ == '__main__':
    config = configparser.ConfigParser()
    config.read('config.ini')

    logging.getLogger().setLevel(logging.INFO)
    logging.info('daemon starting up')
    sys.stdout.flush()
    try:
        asyncio.run(main(config))
    except KeyboardInterrupt:
        pass
    finally:
        logging.info('closing')
