# Gunicorn config for Athena (Pi)
bind = "0.0.0.0:5002"
workers = 2
worker_class = "sync"
timeout = 30

accesslog = "/opt/athena/logs/access.log"
errorlog  = "/opt/athena/logs/error.log"
loglevel  = "info"

max_requests = 1000
max_requests_jitter = 100
