# syntax=docker/dockerfile:1

# Matches the Python version used by the local virtualenv (venv/pyvenv.cfg).
FROM python:3.14-slim

# PYTHONDONTWRITEBYTECODE: keep .pyc files out of the image
# PYTHONUNBUFFERED:        stream logs straight to the container log
# PROMETHEUS_MULTIPROC_DIR: each gunicorn worker writes its metric values here
#                           and /metrics sums them (bookmyshow/metrics.py);
#                           emptied on every start by docker/entrypoint.sh
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc

WORKDIR /app

# MySQL is reached through PyMySQL (registered in bookmyshow/__init__.py), which
# is pure Python - so no gcc / libmysqlclient-dev build toolchain is required.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# PYTHONDONTWRITEBYTECODE stops Python writing .pyc files at runtime, so the
# project's own modules are compiled once here instead of on every start.
RUN python -m compileall -q /app

# Run as an unprivileged user rather than root.
RUN useradd --create-home --uid 1000 appuser \
    && chmod +x /app/docker/entrypoint.sh \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
# The WSGI callable lives in bookmyshow/wsgi.py as `application` - the module is
# `bookmyshow.wsgi`, not `config.wsgi`.
# Workers, threads, port and timeouts live in gunicorn.conf.py.
CMD ["gunicorn", "bookmyshow.wsgi:application", "--config", "gunicorn.conf.py"]
