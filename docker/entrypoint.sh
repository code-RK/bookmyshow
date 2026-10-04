#!/bin/sh
#
# Entrypoint for the bookmyshow web container.
#
#   1. Wait until MySQL accepts connections, so that `migrate` does not race the
#      `db` container on the first boot (a cold MySQL takes ~20-30s to init).
#   2. Apply database migrations.
#   3. Hand over to the container command (CMD).
#
set -e

wait_for_db() {
    # Reads the connection details from Django's settings, so it waits for
    # exactly the database the app will use (the DB_* variables). Exit code
    # 3 means misconfigured - stop instead of waiting forever.
    while true; do
        python -c '
import sys

try:
    from bookmyshow import settings
except Exception as exc:
    print(f"Settings failed to load: {exc}", file=sys.stderr)
    sys.exit(3)

import pymysql

db = settings.DATABASES["default"]
host, port = db["HOST"], int(db["PORT"])
if not host:
    print("No database configured: set DB_HOST, DB_PORT, DB_NAME, DB_USER and DB_PASSWORD.", file=sys.stderr)
    sys.exit(3)
try:
    pymysql.connect(
        host=host,
        port=port,
        user=db["USER"],
        password=db["PASSWORD"],
        ssl_disabled=db["OPTIONS"]["ssl_disabled"],
        connect_timeout=3,
    ).close()
except Exception as exc:
    print(f"Waiting for MySQL at {host}:{port} ... ({exc})", file=sys.stderr)
    sys.exit(1)
' && break
        status=$?
        if [ "$status" -eq 3 ]; then
            exit 1
        fi
        sleep 2
    done
    echo "MySQL is ready."
}

wait_for_db

# Metric files left over from a previous run would be summed into the new
# one's totals, so start from an empty directory.
if [ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]; then
    rm -rf "${PROMETHEUS_MULTIPROC_DIR}"
    mkdir -p "${PROMETHEUS_MULTIPROC_DIR}"
fi

echo "Applying migrations ..."
python manage.py migrate --noinput

# Admin login for creating shows (the API cannot create admins); optional.
if [ -n "${ADMIN_USERNAME:-}" ] && [ -n "${ADMIN_PASSWORD:-}" ]; then
    python manage.py ensure_admin
fi

exec "$@"
