import asyncio
import asyncpg
import logging
import aiomysql
import configparser
from pathlib import Path
from util import config_or_env


async def main(config):
    # postgres config
    #pg_config = config_or_env('PG', config['POSTGRES'], ['host', 'port', 'user', 'password', 'database'])
    #pg_pool = await asyncpg.create_pool(**pg_config)

    #pg_pool.fetch('''
    #  select * from timeclock_shifts where 
    #''')

    mysql_config = config_or_env('MYSQl', config['MYSQL'], ['host', 'port', 'user', 'password', 'db'])
    mysql_pool = await aiomysql.create_pool(**mysql_config)
    async with mysql_pool.acquire() as mysql_conn:
        async with mysql_conn.cursor() as cursor:
            await cursor.execute('select Id,StartTime from tam.polllog order by StartTime desc limit 1')
            print(await cursor.fetchall())

 
if __name__ == '__main__':
    config_path = Path('config.ini')
    if not config_path.is_file():
        raise Exception('config file not found. copy from config.ini.example')
    config = configparser.ConfigParser()
    config.read('config.ini')
    logging.getLogger().setLevel(logging.INFO)

    try:
        asyncio.run(main(config))
    except KeyboardInterrupt:
        pass
