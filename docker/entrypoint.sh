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
    echo "Waiting for MySQL at ${DB_HOST}:${DB_PORT} ..."
    until python -c '
import os
import sys

import pymysql

try:
    conn = pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=3,
    )
    conn.close()
except Exception as exc:
    print(f"  ... not ready: {exc}", file=sys.stderr)
    sys.exit(1)
'; do
        sleep 2
    done
    echo "MySQL is ready."
}

if [ -n "${DB_HOST:-}" ]; then
    wait_for_db
fi

echo "Applying migrations ..."
python manage.py migrate --noinput

exec "$@"
