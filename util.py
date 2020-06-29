import os
import configparser
from typing import List


def config_or_env(prefix: str, config: configparser.ConfigParser, keys: List[str]):
    config = {k: os.environ.get(f'{prefix}_{k.upper()}') or config.get(k) for k in keys}
    if 'port' in config:
        config['port'] = int(config['port'])
    return config
