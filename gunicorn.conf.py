"""Gunicorn configuration tuned for production Flask deployments."""
import multiprocessing
import os


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _default_worker_count() -> int:
    cpu_count = multiprocessing.cpu_count() or 2
    return max(2, (cpu_count * 2) + 1)


def _default_bind() -> str:
    port = (os.environ.get('PORT') or '10000').strip() or '10000'
    return f'0.0.0.0:{port}'


bind = os.environ.get('GUNICORN_BIND') or _default_bind()
backlog = int(os.environ.get('GUNICORN_BACKLOG', '2048'))

worker_class = os.environ.get('GUNICORN_WORKER_CLASS') or (
    'gevent' if os.environ.get('FLASK_ENV') == 'production' else 'sync'
)
workers = int(os.environ.get('WEB_CONCURRENCY') or _default_worker_count())
worker_connections = int(os.environ.get('GUNICORN_WORKER_CONNECTIONS', '1000'))
threads = int(os.environ.get('GUNICORN_THREADS', '1'))

timeout = int(os.environ.get('GUNICORN_TIMEOUT', '60'))
graceful_timeout = int(os.environ.get('GUNICORN_GRACEFUL_TIMEOUT', '30'))
keepalive = int(os.environ.get('GUNICORN_KEEPALIVE', '5'))

accesslog = os.environ.get('GUNICORN_ACCESS_LOG', '-')
errorlog = os.environ.get('GUNICORN_ERROR_LOG', '-')
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
capture_output = True
access_log_format = (
    '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s '
    '"%(f)s" "%(a)s" %(D)s'
)

proc_name = 'retroquest'
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None
preload_app = _as_bool(os.environ.get('GUNICORN_PRELOAD'), False)
forwarded_allow_ips = os.environ.get('FORWARDED_ALLOW_IPS', '*')

max_requests = int(os.environ.get('MAX_REQUESTS', '1000'))
max_requests_jitter = int(os.environ.get('MAX_REQUESTS_JITTER', '100'))

reload = os.environ.get('FLASK_ENV') == 'development'
reload_engine = 'auto'
