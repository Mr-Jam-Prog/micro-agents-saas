"""
Configuration Gunicorn pour production.
Utilisé si vous choisissez Gunicorn au lieu d'Uvicorn workers.
"""

import multiprocessing
import os

# Configuration de base
bind = f"{os.getenv('UVICORN_HOST', '0.0.0.0')}:{os.getenv('UVICORN_PORT', '8000')}"
workers = int(os.getenv('UVICORN_WORKERS', multiprocessing.cpu_count() * 2 + 1))
worker_class = 'uvicorn.workers.UvicornWorker'
worker_connections = 1000
timeout = 30
keepalive = 2

# Logging
accesslog = '-'
errorlog = '-'
loglevel = os.getenv('LOG_LEVEL', 'info').lower()
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(L)s'

# Performance
max_requests = int(os.getenv('GUNICORN_MAX_REQUESTS', 1000))
max_requests_jitter = int(os.getenv('GUNICORN_MAX_REQUESTS_JITTER', 50))
graceful_timeout = int(os.getenv('GUNICORN_GRACEFUL_TIMEOUT', 30))

# Security
forwarded_allow_ips = '*'
proxy_protocol = True
proxy_allow_ips = '*'

# Process naming
proc_name = 'microagents-api'

# Worker tuning
worker_tmp_dir = '/dev/shm'
threads = 1

# StatsD metrics (optionnel)
if os.getenv('STATSD_HOST'):
    statsd_host = f"{os.getenv('STATSD_HOST')}:{os.getenv('STATSD_PORT', '8125')}"
    statsd_prefix = os.getenv('STATSD_PREFIX', 'microagents.api')

# Hook pour le démarrage
def on_starting(server):
    """Hook exécuté au démarrage du master."""
    server.log.info("MicroAgents API starting...")

def post_fork(server, worker):
    """Hook exécuté après le fork de chaque worker."""
    server.log.info(f"Worker spawned (pid: {worker.pid})")

def worker_int(worker):
    """Hook exécuté quand un worker reçoit un SIGINT ou SIGTERM."""
    worker.log.info("Worker received INT or TERM signal")

def worker_abort(worker):
    """Hook exécuté quand un worker reçoit un SIGABRT."""
    worker.log.info("Worker received SIGABRT")

def pre_exec(server):
    """Hook exécuté avant le fork du master."""
    server.log.info("Forking children...")

def pre_fork(server, worker):
    """Hook exécuté juste avant le fork de chaque worker."""
    pass

def post_worker_init(worker):
    """Hook exécuté après l'initialisation de chaque worker."""
    # Initialisation des connexions aux bases de données, etc.
    pass

def worker_exit(server, worker):
    """Hook exécuté quand un worker se termine."""
    server.log.info(f"Worker exiting (pid: {worker.pid})")

def nworkers_changed(server, new_value, old_value):
    """Hook exécuté quand le nombre de workers change."""
    server.log.info(f"Number of workers changed from {old_value} to {new_value}")

def on_exit(server):
    """Hook exécuté quand Gunicorn se termine."""
    server.log.info("MicroAgents API shutting down...")