from python:3.8
workdir /app
copy requirements.txt .
run pip install -r requirements.txt
copy . .
cmd ["python3", "-u", "daemon.py"]
