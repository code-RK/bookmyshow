#!/bin/sh
# One-command on-sale stampede against a running service:
#
#   ADMIN_USERNAME=admin ADMIN_PASSWORD=... ./burst.sh http://localhost:8000
#   ./burst.sh https://your-app.example.com --requests 20000 --concurrency 300
#
# Extra flags go straight to scripts/burst.py (see `./burst.sh --help`).
set -e
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
exec "$PY" scripts/burst.py "$@"
