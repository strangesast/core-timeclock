#!/bin/bash
gunicorn ws:main --bind 0.0.0.0:80 --worker-class aiohttp.GunicornWebWorker
