# syntax=docker/dockerfile:1

# Matches the Python version used by the local virtualenv (venv/pyvenv.cfg).
FROM python:3.14-slim

# PYTHONDONTWRITEBYTECODE: keep .pyc files out of the image
# PYTHONUNBUFFERED:        stream logs straight to the container log
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# MySQL is reached through PyMySQL (registered in bookmyshow/__init__.py), which
# is pure Python - so no gcc / libmysqlclient-dev build toolchain is required.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user rather than root.
RUN useradd --create-home --uid 1000 appuser \
    && chmod +x /app/docker/entrypoint.sh \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
# The WSGI callable lives in bookmyshow/wsgi.py as `application` - the module is
# `bookmyshow.wsgi`, not `config.wsgi`.
CMD ["gunicorn", "bookmyshow.wsgi:application", "--bind", "0.0.0.0:8000"]
