# connect to AMG server / mysql
# poll on interval / based on device schedule
# send updates to mongo, tailable collection


# init.py
import os
import sys
import pytz
import logging
import asyncio
import asyncpg
import xmlrpc.client
import aiomysql
import configparser
from typing import List
from pathlib import Path
from datetime import datetime, timedelta
from aiohttp_xmlrpc.client import ServerProxy


import models
#from calculate_rows import recalculate
from util import config_or_env

from pprint import pprint

tz = pytz.timezone('US/Eastern')


#COLORS = ["#d73027", "#f46d43", "#fdae61", "#fee090", "#e0f3f8", "#abd9e9", "#74add1", "#4575b4"]
#COLORS = ["#a6cee3", "#1f78b4", "#b2df8a", "#33a02c", "#fb9a99", "#e31a1c", "#fdbf6f", "#ff7f00", "#cab2d6", "#6a3d9a"]
COLORS = ["#1f78b4", "#33a02c", "#e31a1c", "#ff7f00", "#6a3d9a"]

async def sync_employees(mysql_pool, pg_pool):
    ''' sync amg mysql table w/ postgres, changing some column names
    '''
    ekeys = ['id', 'Name', 'MiddleName', 'LastName', 'HireDate', 'Code']
    tkeys = ['id', 'first_name', 'middle_name', 'last_name', 'hire_date', 'code']
    count = 0;
    async with mysql_pool.acquire() as mysql_conn, pg_pool.acquire() as pg_conn:
        async with mysql_conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute('select id,Name,MiddleName,LastName,HireDate,Code from tam.inf_employee')
            async for record in cursor:
                # id, first_name, middle_name, last_name, hire_date, code 
                q = f'''
                  insert into users(employee_id, username, color, password)
                  values($1, $2, $3, crypt($4, gen_salt(\'bf\')))
                  on conflict do nothing
                  returning id
                '''
                employee_id, first_name, last_name, code = [record[k] for k in ['id', 'Name', 'LastName', 'Code']]
                username = (first_name[0:1] + last_name).lower()
                color = COLORS[employee_id % len(COLORS)]
                user_record = await pg_conn.fetchrow(q, employee_id, username, color, code)
                if user_record is not None:
                    q = f'insert into user_roles(user_id, role_id) values($1, $2)'
                    await pg_conn.fetch(q, user_record['id'], 'isPaidHourly')
                else:
                    user_record = await pg_conn.fetchrow('select id from users where employee_id=$1', employee_id)
                user_id = user_record['id']

                q = f'''
                  insert into employees (id,first_name,middle_name,last_name,hire_date,code,user_id,color)
                  values ($1,$2,$3,$4,$5,$6,$7,$8)
                  on conflict (id) do update set {", ".join([f"{k} = excluded.{k}" for k in tkeys[1:]])}
                  returning id;
                '''
                await pg_conn.fetch(q, *[record[k] for k in ekeys], user_id, color)


                count += 1
    logging.info(f'inserted/updated {count} employees')


async def sync_timeclock(proxy, pg_pool, date_range):
    min_date, max_date = date_range
    interval = timedelta(days=14)

    keys = ['employee_id', 'date_start', 'date', 'date_stop', 'punch_start',
            'punch_stop', 'is_manual', 'sync_id', 'last_modified']
    q = f'''
      insert into timeclock_shifts ({",".join(keys)})
      values ({",".join([f"${i+1}" for i in range(len(keys))])})
      on conflict (employee_id,date_start) do update set {", ".join([f"{k} = excluded.{k}" for k in keys[2:]])};
    '''
    count = 0
    sync_job_status = 'in_progress'
    error_msg = ''

    start_date = datetime.utcnow()
    record = await pg_pool.fetchrow('''
        insert into timeclock_sync (start_date, job_status, is_manual)
        values ($1, $2, $3) returning id''', start_date, sync_job_status, False)
    sync_id = record['id']
 
    try:
        async with pg_pool.acquire() as pg_conn:
            async with pg_conn.transaction():
                records = await pg_conn.fetch('select id from employees')
                employee_ids = [r['id'] for r in records]


                while min_date < max_date:
                    window_date = min_date + interval
                    logging.info(f'{min_date} - {window_date}')

                    timecards = await proxy.GetTimecards(employee_ids, min_date, window_date, False)
                    records = []

                    for employee_id, timecard in [(obj['EmployeeId'], timecard) for obj in timecards for timecard in obj['Timecards']]:
                        punches = []
                        date, is_manual, hours = timecard.get('Date'), timecard.get('IsManual'), timecard.get('Reg')
                
                        (date_start, punch_start), (date_stop, punch_stop) = [(reset_tz(p['OriginalDate']), p['Id'])
                                    if (p := timecard.get(k))
                                    else [None, None]
                                    for k in ['StartPunch', 'StopPunch']]
                
                        # seems to happen when hours are added manually
                        if punch_start is None:
                            continue
                        records.append([employee_id, date_start, date, date_stop,
                            punch_start, punch_stop, is_manual, sync_id, date_start])

                    if len(records):
                        count += len(records)
                        await pg_conn.executemany(q, records)

                    logging.info(f'inserted/updated {len(records)} shifts')

                    min_date = window_date
        sync_job_status = 'complete'

    except Exception as e:
        sync_job_status = 'errored'
        error_msg = 'error: ' + str(e)

    finally:
        complete_date = datetime.utcnow()
        await pg_pool.fetch('REFRESH MATERIALIZED VIEW timeclock_shifts_view')
        await pg_pool.fetch('REFRESH MATERIALIZED VIEW timeclock_shift_groups')
        record = await pg_pool.fetch('''
          update timeclock_sync set
            complete_date=$2,
            job_status=$3,
            update_count=$4,
            note=$5
          where id=$1
        ''', sync_id, complete_date, sync_job_status, count, error_msg)

    return complete_date


async def flag_shifts():
    query = '''
      select *
      from (
      	select id, employee_id, (case when date_stop is null then now() else date_stop end) - date_start as duration
      	from timeclock_shifts
      ) t
      where t.duration > interval '12 hours'
      order by t.duration desc
    '''


def reset_tz(dt: datetime):
    return tz.localize(dt).astimezone(pytz.UTC).replace(tzinfo=None)


def get_sunday(dt: datetime):
    dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    dt = dt - timedelta(days=(dt.weekday() + 1) % 7)
    return dt


async def main(config):
    # mysql config
    mysql_config = config_or_env('MYSQl', config['MYSQL'], ['host', 'port', 'user', 'password', 'db'])
    mysql_pool = await aiomysql.create_pool(**mysql_config, autocommit=True)

    # amg xmlrpc config
    amg_keys = ['user', 'password', 'host', 'port']
    amg_config = config_or_env('AMG', config['AMG'], amg_keys)
    uri = 'http://{}:{}@{}:{}/API/Timecard.ashx'.format(*[amg_config[k] for k in amg_keys])
    proxy = ServerProxy(uri)

    # postgres config
    pg_config = config_or_env('PG', config['POSTGRES'], ['host', 'port', 'user', 'password', 'database'])
    pg_pool = await asyncpg.create_pool(**pg_config)

    await pg_pool.fetch('''
      update timeclock_sync
      set job_status = 'errored', complete_date = now(), note = 'unknown error'
      where job_status = 'in_progress'
    ''')

    await sync_employees(mysql_pool, pg_pool)

    interval = timedelta(hours=1)
    buf = 60 # 1 minute. added to interval, or used as timeout between retries


    async with pg_pool.acquire() as pg_conn:
        record = await pg_conn.fetchrow('select date from timeclock_polls order by date desc limit 1');
        latest_poll = None if record is None else record['date']
        record = await pg_conn.fetchrow('''
          select complete_date from timeclock_sync
          where job_status=$1 and complete_date is not null
          order by complete_date desc
          limit 1''', 'complete');
        latest_sync = None if record is None else record['complete_date']

    while True:
        async with mysql_pool.acquire() as mysql_conn, pg_pool.acquire() as pg_conn:
            async with mysql_conn.cursor() as cursor:
                await cursor.execute('select Id,StartTime from tam.polllog order by StartTime desc limit 4')
                if latest_poll:
                    await cursor.execute('select Id,StartTime from tam.polllog where StartTime > %s order by StartTime desc',
                            (latest_poll + tz.utcoffset(latest_poll),))
                else:
                    await cursor.execute('select Id,StartTime from tam.polllog order by StartTime desc')
                    
                if cursor.rowcount:
                    polls = [(_id, tz.localize(date).astimezone(pytz.UTC).replace(tzinfo=None))
                        for _id, date, in await cursor.fetchall()]
                    logging.info(f'got {len(polls)} new polls')
                    latest_poll = polls[0][1]
                    q = f'insert into timeclock_polls (id, date) values ($1, $2)'
                    await pg_conn.executemany(q, polls)

            #record = await pg_pool.fetchrow('select date from timeclock_polls order by date desc limit 1');
            #latest_poll = None if record is None else record['date']

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

        latest_sync = await sync_timeclock(proxy, pg_pool, [min_date, now])

        #await mongo_db.state.insert_one({'date': now, 'values': values})
        #await recalculate(mongo_db, min_date)

        latest_sync = now

    await proxy.close()
    mysql_pool.close()
    await mysql_pool.wait_closed()
    await pg_pool.close()


if __name__ == '__main__':
    config_path = Path('config.ini')
    if not config_path.is_file():
        raise Exception('config file not found. copy from config.ini.example')
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
