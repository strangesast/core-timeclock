# util.py
import os
import logging
from datetime import timedelta


async def get_mysql_db(config):
    import aiomysql
    '''
    connect to mysql/mariadb database
    '''
    host, port, user, password, db = [os.environ.get(f'MYSQL_{k.upper()}') or config.get(k) for k in ['host', 'port', 'user', 'password', 'db']]
    port = int(port)
    conn = await aiomysql.connect(host=host, port=port, user=user, password=password)
    return conn


async def get_mongo_db(config):
    import motor.motor_asyncio
    from pymongo.errors import ConnectionFailure
    '''
    connect to mongodb, check connection
    '''
    host, port, user, password = [os.environ.get(f'MONGO_{k.upper()}') or config.get(k) for k in ['host', 'port', 'user', 'password']]
    url = f'mongodb://{host}:{port}'
    conn = motor.motor_asyncio.AsyncIOMotorClient(url)

    logging.info(f'connecting to mongo at {url}')
    try:
        await conn.admin.command('ismaster')
    except ConnectionFailure as e:
        raise Exception(f'failed to connect to mongo at "{url}"')

    return conn


def get_async_rpc_connection(config):
    import asyncio
    import xmlrpc.client
    from aiohttp_xmlrpc.client import ServerProxy
    host, port, username, password = [os.environ.get(f'amg_{k}'.upper()) or config.get(k)
            for k in ['host', 'port', 'username', 'password']]
    loop = asyncio.get_running_loop()
    uri = f'http://{username}:{password}@{host}:{port}/API/Timecard.ashx'
    return ServerProxy(uri, loop=loop)


def get_rpc_connection(host='localhost', port=3003, password='password', username='admin'):
    uri = f'http://{username}:{password}@{host}:{port}/API/Timecard.ashx'
    return xmlrpc.client.ServerProxy(uri, use_datetime=True)


def merge_dups(arr):
    ''' arr is pairs of clock in, clock out
        combine sequential pairs with same clock out, clock in
    '''
    # if empty, return empty
    if not arr: return
    # start off with first pair
    aa, ab = arr[0]
    for ba, bb in arr[1:]:
        # if pair A end is pair B start
        if ba == ab:
            # set pair A end to pair B end
            ab = bb
        else:
            # else pair A is unique so move on
            yield (aa, ab)
            # set pair B as pair A
            aa, ab = ba, bb
    yield (aa, ab)


def merge_nearby_shifts(it, threshold=timedelta(hours=4)):
    last_end = None
    components = []
    for shift in it:
        start_date, end_date = shift
        if last_end is not None and (start_date - last_end) < threshold:
            components.append(shift)
        else:
            if len(components):
                yield components

            components = [shift]

        last_end = end_date

    if len(components):
        yield components


def first_transform(it, offset=timedelta(hours=5)):
    for item in it:
        obj = {'Punches': []}
        for k in ['Date', 'IsManual', 'Reg']:
            obj[k] = item.get(k)
        for k in ['StartPunch', 'StopPunch']:
            if (p := item.get(k)):
                obj[k] = p['OriginalDate'] + offset
                obj['Punches'].append(p['Id'])
            else:
                obj[k] = None
        yield obj


def rename_fields(it):
    for item in it:
        obj = {}
        obj['punches'] = item['Punches']
        obj['date'] = item['Date']
        obj['isManual'] = item['IsManual']
        obj['start'] = item['StartPunch']
        obj['end'] = item['StopPunch']
        obj['isMerged'] = item['IsMerged']
        yield obj;


def second_transform(it):
    try:
        obj = next(it)
    except StopIteration:
        return

    timecard = obj
    timecard['IsMerged'] = False
    aa, ab = [obj.get(k) for k in ['StartPunch', 'StopPunch']]
    second = False

    for nextobj in it:
        ba, bb = [nextobj.get(k) for k in ['StartPunch', 'StopPunch']]
        # if pair A end is pair B start
        if ba == ab:
            # set pair A end to pair B end
            timecard['Reg'] += nextobj['Reg']
            timecard['Punches'].extend(nextobj['Punches'])
            timecard['StopPunch'] = nextobj['StopPunch']
            timecard['IsMerged'] = True
            ab = bb
            second = False
        else:
            # else pair A is unique so move on
            yield timecard
            timecard = nextobj
            timecard['IsMerged'] = False
            # set pair B as pair A
            aa, ab = ba, bb
            second = True

    yield timecard


def third_transform(it, threshold=timedelta(hours=4)):
    last_end = None
    group = []
    for item in it:
        start_date, end_date = item['start'], item['end']
        if start_date is None: # not sure how to handle this
            continue
        if last_end is not None and (start_date - last_end) < threshold:
            group.append(item)
        else:
            if len(group):
                yield group

            group = [item]

        last_end = end_date

    if len(group):
        yield group


def parse_timecards_2(employee_id, timecards, offset=timedelta(hours=5)):
    yield from third_transform(rename_fields(second_transform(first_transform(timecards))))


def parse_timecards(employee_id, timecards, offset=timedelta(hours=5)):
    shifts = []

    timecards = [[punch['OriginalDate'] + offset if (punch := timecard.get(key)) else None for key in ('StartPunch', 'StopPunch')]
            for timecard in timecards]

    timecards = merge_dups(timecards)
    # not sure why start would be None
    shifts = [(start, end) for start, end in timecards if start is not None]

    return shifts


from enum import IntEnum, Enum
from typing import TypedDict, List, Dict
from datetime import datetime, timedelta


class EmployeeShiftColor(IntEnum):
    BLUE = 0
    GREEN = 1
    RED = 2
    ORANGE = 3
    PINK = 4


class ShiftState(str, Enum):
    Complete = 'complete'
    Incomplete = 'incomplete'


class ShiftComponentType(str, Enum):
    Actual = 'actual'
    Projected = 'projected'


class ShiftComponent(TypedDict):
    start: datetime
    end: datetime
    duration: timedelta
    state: ShiftState


class Shift(TypedDict):
    start: datetime
    end: datetime
    duration: int
    employeeId: str
    components: List[ShiftComponent]


class Employee(TypedDict):
    id: str
    name: str
    color: EmployeeShiftColor


class ShiftsResponse(TypedDict):
    shifts: List[Shift]
    employees: Dict[str, Employee]
    employeeIds: List[str]


class EmployeeShiftsResponse(TypedDict):
    employee: Employee
    shifts: List[Shift]
