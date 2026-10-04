"""gunicorn settings for the web container (used by the Dockerfile CMD).

Concurrency is workers x threads requests at once. Every thread keeps its own
MySQL connection (CONN_MAX_AGE in settings.py), so workers x threads must stay
well under MySQL's max_connections (151 by default): the default 4 x 8 = 32
leaves room for Prometheus scrapes, migrations and admin access.
"""

import os

from prometheus_client import multiprocess

# PaaS platforms (Railway, Render, Fly.io) pass the port to listen on as $PORT.
bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# gthread: each worker process serves requests on a pool of threads. The work
# is mostly waiting on MySQL, which releases the GIL, so threads overlap well.
worker_class = "gthread"
workers = int(os.environ.get("GUNICORN_WORKERS", "4"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))

# Connections waiting to be accepted while every thread is busy (default
# 2048); a burst queues here instead of being refused.
backlog = 4096

# A request taking longer than this is killed (default 30s). Requests are
# normally milliseconds; this only bounds a pathological one.
timeout = 60
graceful_timeout = 30
keepalive = 5

# No access log: RequestLogMiddleware already writes one JSON line per request
# (with its request id). gunicorn's own messages - startup, worker boots,
# timeouts - use the same JSON format, on stdout.
accesslog = None
logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "bookmyshow.log.JsonFormatter"}},
    "handlers": {
        "stdout": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"},
    },
    "root": {"level": "INFO", "handlers": ["stdout"]},
    "loggers": {
        "gunicorn.error": {"level": "INFO", "handlers": ["stdout"], "propagate": False, "qualname": "gunicorn.error"},
        "gunicorn.access": {"level": "INFO", "handlers": [], "propagate": False, "qualname": "gunicorn.access"},
    },
}

# Import Django and the project once in the master process; forked workers
# share the loaded modules instead of each importing everything again. This
# makes worker start-up (and a cold deploy's first burst) much cheaper. Safe
# because nothing opens a database connection at import time.
preload_app = True


def when_ready(server):
    # Runs in the master after the app is preloaded and before workers are
    # forked: resolving the URLconf imports every view module now, rather
    # than on each worker's first request.
    from django.urls import get_resolver

    get_resolver().url_patterns


def child_exit(server, worker):
    # Lets /metrics drop the live-gauge files of a worker that has exited
    # (counters and histograms from it are kept, so totals never go down).
    multiprocess.mark_process_dead(worker.pid)
