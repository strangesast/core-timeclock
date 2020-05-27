#!/bin/bash
gunicorn api:main --bind 0.0.0.0:80 --worker-class aiohttp.GunicornWebWorker
