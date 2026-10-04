# bookmyshow
Scalable backend service to enable booking of a show and survives stampede requests.

## Tech stack

| Component | Choice |
| --- | --- |
| Language | Python 3.14 |
| Framework | Django 6.0.7 |
| API layer | Django REST Framework + SimpleJWT |
| Database | MySQL (accessed through PyMySQL) |
| Containers | Docker / Docker Compose |

## Project layout

```
bookmyshow/                # repository root
├── manage.py
├── requirements.txt
├── Dockerfile             # web image (python:3.14-slim)
├── docker-compose.yml     # db (mysql:8.4) + web services
├── docker/
│   └── entrypoint.sh      # waits for MySQL, migrates, then starts Django
├── .env.example           # template for the git-ignored .env
├── bookmyshow/            # Django project package
│   ├── __init__.py        # registers PyMySQL as MySQLdb
│   ├── settings.py        # reads DB_* from .env via python-dotenv
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── accounts/              # auth app (register / login)
│   ├── models/            # package, not models.py
│   │   ├── __init__.py    # re-exports Tbl_Users so Django registers it
│   │   └── Tbl_Users.py   # Tbl_Users - the AUTH_USER_MODEL (table tbl_users)
│   ├── serializers.py     # RegisterSerializer / LoginSerializer
│   ├── permissions.py     # IsAdminRole - gates the admin-only endpoints
│   ├── views.py
│   ├── urls.py
│   ├── admin.py
│   └── migrations/
├── show/                  # shows app
│   ├── models/            # package, not models.py
│   │   ├── __init__.py    # star-imports the four tables so Django registers them
│   │   ├── Tbl_Show.py        # name, price_paise, per_user_limit -> tbl_show
│   │   ├── Tbl_Seat.py        # seat_number, status -> tbl_seat
│   │   ├── Tbl_Reservation.py # -> tbl_reservation (booking flow, not live yet)
│   │   └── Tbl_Reservation_Seat.py # -> tbl_reservation_seat
│   ├── serializers.py     # ShowCreateSerializer / ShowSerializer
│   ├── views.py           # ShowCreateView (admin) / ShowDetailView
│   ├── urls.py
│   ├── admin.py
│   └── migrations/
├── docs/                  # db-architecture.md, api-contract.md
└── venv/                  # local virtualenv (git-ignored)
```

## Local setup

1. Activate the virtualenv and install dependencies:

   ```powershell
   .\venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Create the MySQL database:

   ```sql
   CREATE DATABASE IF NOT EXISTS bookmyshow
       CHARACTER SET utf8mb4
       COLLATE utf8mb4_unicode_ci;
   ```

3. Create your `.env` from the template and fill in the credentials
   (`bookmyshow/settings.py` loads it through python-dotenv):

   ```powershell
   Copy-Item .env.example .env
   ```

   ```
   DB_NAME=bookmyshow
   DB_USER=root
   DB_PASSWORD=<your-mysql-password>
   DB_HOST=127.0.0.1
   DB_PORT=3306
   ```

4. Apply migrations and start the dev server:

   ```powershell
   python manage.py migrate
   python manage.py runserver
   ```

## Authentication

JWT auth via SimpleJWT. DRF is configured with `JWTAuthentication` and
`IsAuthenticated` as the **project defaults**, so any new endpoint is protected
unless it explicitly opts out with `permission_classes = (AllowAny,)`.

| Method | Path | Auth | Request body | Success |
| --- | --- | --- | --- | --- |
| POST | `/api/v1/auth/register` | public | `{"username", "password"}` (`email` optional) | `201 {"message": "user created successfully"}` |
| POST | `/api/v1/auth/login` | public | `{"username", "password"}` | `200 {"access", "refresh"}` |

Both paths have no trailing slash, matching `docs/api-contract.md` exactly
(a trailing slash would make Django's `APPEND_SLASH` turn the POST into a 301).

Register a user and use the access token on protected endpoints:

```powershell
Invoke-RestMethod -Method POST -Uri http://localhost:8000/api/v1/auth/register `
    -ContentType 'application/json' `
    -Body '{"username":"test","password":"pass@123"}'

$token = (Invoke-RestMethod -Method POST -Uri http://localhost:8000/api/v1/auth/login `
    -ContentType 'application/json' `
    -Body '{"username":"test","password":"pass@123"}').access

# Protected endpoints take the bearer token:
Invoke-RestMethod -Uri http://localhost:8000/api/v1/shows/1 `
    -Headers @{ Authorization = "Bearer $token" }
```

- Access tokens last **30 minutes**, refresh tokens **1 day** (`SIMPLE_JWT` in
  `settings.py`); SimpleJWT's own defaults are 5 minutes / 1 day.
- Passwords are hashed by `create_user()` and validated against Django's
  `AUTH_PASSWORD_VALIDATORS` during registration.
- Duplicate `username` → `400`; wrong credentials → `401`.
- Registration and login run against the project's own user model,
  `accounts.models.Tbl_Users` (table `tbl_users`), declared as
  `AUTH_USER_MODEL` in `settings.py`. It subclasses `AbstractUser` and adds the
  `role` column described in `docs/db-architecture.md`.
- `role` defaults to `customer` and is **not** writable through the register
  endpoint — otherwise anyone could register themselves as an admin. Promote a
  user to `admin` from `/admin/` (`accounts/models/Tbl_Users.py`).

## Show endpoints

| Method | Path | Auth | Request body | Success |
| --- | --- | --- | --- | --- |
| POST | `/api/v1/shows` | **admin role** (`role == admin`) | `{"name", "seats": ["A1", "A2"], "price_paise"}` | `201` show (same shape as GET) |
| GET | `/api/v1/shows/{show_id}` | any authenticated user | — | `200 {"id", "name", "price_paise", "per_user_limit", "total_seats", "counts", "seats"}` |
| POST | `/api/v1/shows/{show_id}/reserve` | any authenticated user | `{"seats": ["A1"]}` + idempotency key (`Idempotency-Key` header or `idempotency_key` field) | `201 {"reservation_id", "show_id", "user_id", "seats", "amount_paise", "status"}` |
| GET | `/api/v1/reservations/{reservation_id}` | owner only | — | `200` reservation |
| POST | `/api/v1/reservations/{reservation_id}/cancel` | owner only | — | `200` cancelled reservation |

All require `Authorization: Bearer <access token>`; that is the only auth
mechanism, because `SessionAuthentication` is not configured. Failures follow
`docs/api-contract.md`: missing or invalid token → `401`, authenticated
non-admin POST → `403`, unknown show (or someone else's reservation) → `404`,
malformed body → `400`, seat taken / per-user limit / reused idempotency
key → `409`.

`counts` holds `available`, `held` and `confirmed`, and always sums to
`total_seats`. `held` is always `0`: reservations confirm their seats
immediately and are released by cancelling. Reservations are
all-or-nothing: if any requested seat is taken, nothing is booked.

`POST /api/v1/shows` writes the show row and one `tbl_seat` row per supplied
seat number inside a single transaction, so a rejected request leaves nothing
behind. Duplicate seat numbers (compared case-insensitively, because MySQL's
default collation is too), an empty seat list and a non-positive `price_paise`
are all rejected with `400`.

```powershell
# Promote the user to admin from /admin/ first - role is not writable via the API.
$token = (Invoke-RestMethod -Method POST -Uri http://localhost:8000/api/v1/auth/login `
    -ContentType 'application/json' `
    -Body '{"username":"admin","password":"pass@123"}').access

Invoke-RestMethod -Method POST -Uri http://localhost:8000/api/v1/shows `
    -ContentType 'application/json' `
    -Headers @{ Authorization = "Bearer $token" } `
    -Body '{"name":"friday-night","seats":["A1","A2","A3"],"price_paise":25000}'

Invoke-RestMethod -Uri http://localhost:8000/api/v1/shows/1 `
    -Headers @{ Authorization = "Bearer $token" }
```

## Docker setup

Runs the Django service and a MySQL 8.0 database (Django 6.0 requires MySQL
>= 8.0.11) with a single command. Credentials come from the same `.env` file
used by local runs, so there is one source of truth.

```powershell
docker compose up --build     # start db + web
docker compose logs -f web    # follow application logs
docker compose down           # stop the stack (keeps the database volume)
docker compose down -v        # stop and wipe the database volume
```

- Web app: <http://localhost:8000> - port `8000` is published.
- The `db` port is **not** published, because your host already runs MySQL on
  3306; `web` reaches it over the Compose network as `db:3306`. Uncomment the
  `ports:` block in `docker-compose.yml` to expose it (e.g. `13306:3306`).
- `docker/entrypoint.sh` waits until MySQL accepts connections, runs
  `manage.py migrate --noinput`, then `exec`s gunicorn
  (`bookmyshow.wsgi:application`) with the settings in `gunicorn.conf.py`.
- gunicorn runs `GUNICORN_WORKERS` processes × `GUNICORN_THREADS` threads
  (default 4 × 8 = 32 requests at once; each thread holds one MySQL
  connection, so keep the product well under MySQL's `max_connections`,
  151 by default). It listens on `$PORT` (default 8000), as PaaS hosts expect.
- `DB_SSL=false` (the Compose default) turns off TLS between `web` and `db`,
  which share a private network; leave it on for a database reached over the
  internet.
- Prometheus counters from all workers are summed through
  `PROMETHEUS_MULTIPROC_DIR` (set in the Dockerfile, emptied on every start).
- The `db` service sets `MYSQL_ROOT_HOST: "%"`, because the app authenticates
  from the `web` container rather than from localhost. This only applies when
  the `mysql_data` volume is first created - run `docker compose down -v` if
  you add it after an earlier boot. For anything beyond local development,
  create a dedicated non-root user with `MYSQL_USER`/`MYSQL_PASSWORD` instead.

Override any value for a single run without editing files:

```powershell
$env:DB_NAME = "bookmyshow_dev"; docker compose up --build
```

### Why these choices

- Base image `python:3.14-slim` matches `venv/pyvenv.cfg`, and because PyMySQL
  is pure Python no compiler or `libmysqlclient-dev` is needed in the image.
- `cryptography` is pinned in `requirements.txt`: MySQL 8.x uses
  `caching_sha2_password`, and PyMySQL requires it for the first full
  authentication over an unencrypted connection (a fresh container's
  authentication cache is always cold).
- `.env` is listed in `.dockerignore`, so Compose injects secrets at runtime
  instead of baking them into an image layer.
- `gunicorn` is pinned in `requirements.txt` as the container's WSGI server.
  Because the image only installs from `requirements.txt`, a server that is
  installed locally but missing there fails with
  `exec: gunicorn: not found` and the container restart-loops with exit 127.

## Logs

Logs go to stdout as one JSON object per line: `docker compose logs -f web`
locally, or the hosting platform's log viewer. Every request is logged exactly
once, when its response is ready:

```json
{"ts": "2026-10-04T09:23:38.871Z", "level": "INFO", "logger": "api.request",
 "msg": "POST /api/v1/shows/35/reserve 201", "request_id": "e5f9db1a5c6a4b01a3279ef22e268e09",
 "method": "POST", "path": "/api/v1/shows/35/reserve", "route": "/api/v1/shows/<int:show_id>/reserve",
 "status": 201, "duration_ms": 28.6, "user_id": 26510, "show_id": 35,
 "outcome": "confirmed", "reservation_id": 23119, "seats": ["A1", "A2"]}
```

- **Request id**: the caller's `X-Request-ID` header if it sent one
  (letters, digits, `.`, `_`, `-`; up to 64 characters), otherwise a new one.
  It is returned in the `X-Request-ID` response header and appears on every
  line written during that request, including the traceback of a 500.
- **outcome** says what happened. On reserve it is `confirmed`, `replayed` or
  `declined` (with `reason`, the same names as the 409 body and the metrics).
  On cancel it is `cancelled` (with the `released` seats) or
  `already_cancelled`. Creating a show logs `show_created`. Any 4xx/5xx also
  carries `error`, the message from the response.
- `/metrics` and `/health/*` are logged only when they fail, since they are
  polled constantly.
- A deadlock or lock timeout that is retried logs a WARNING from
  `api.reservations` with the MySQL error code and attempt number.

Settings: `LOG_LEVEL` (default `INFO`) and `LOG_FORMAT` (`json`, the default,
or `text` for a readable local terminal).

Useful filters:

```bash
docker compose logs web --no-log-prefix | grep '"status": 5'           # server errors
docker compose logs web --no-log-prefix | grep '"reason": "seats_unavailable"'
docker compose logs web --no-log-prefix | grep '"request_id": "<id>"'   # one request
```

## Burst test (on-sale stampede)

One command fires an on-sale stampede at a running service, prints the outcome
distribution, and checks the results against the API and `/metrics`:

```bash
ADMIN_USERNAME=admin ADMIN_PASSWORD=... ./burst.sh http://localhost:8000
ADMIN_USERNAME=admin ADMIN_PASSWORD=... ./burst.sh https://<live-url> --requests 20000 --concurrency 300
```

```powershell
# Windows / PowerShell
$env:ADMIN_USERNAME = "admin"; $env:ADMIN_PASSWORD = "..."
python scripts/burst.py http://localhost:8000
```

It needs only Python 3.9+ (standard library) and the admin login that
`docker/entrypoint.sh` creates from `ADMIN_USERNAME` / `ADMIN_PASSWORD`. Each run
creates its own show (default 500 seats), so runs never interfere. Phases:

1. **setup**: log in or register the test users (default 200). Logins hash
   a password on purpose, so they happen before the clock starts; the first run
   against a new deployment also registers them and takes longer.
2. **stampede** (default 20,000 requests, 200 in flight), all shuffled
   together:
   - a quarter of the requests storm 5 hot seats;
   - the rest are general traffic for 1-2 seats;
   - 10% are retries that resend an earlier request with the same idempotency
     key;
   - one user fires 10 parallel requests at a limit-4 show;
   - a user claims someone else's `user_id` in the body.
3. **idempotency**: a confirmed key resent with the same body (must replay)
   and with different seats (must be 409).
4. **release**: hot-seat winners cancel (while another user tries to cancel
   the same reservations), then each freed seat is stormed again.
5. **reconciliation**: zero 5xx, exactly one winner per contested seat, no
   seat in two live reservations, the API's confirmed seats equal the seats in
   the 201 responses, no user over the limit, one reservation per idempotency
   key, `available + held + confirmed == total_seats`, and the `/metrics` seat
   gauges equal `GET /shows/{id}`.

The script exits with status 1 if any check fails. `./burst.sh --help` lists
the knobs (`--requests`, `--concurrency`, `--seats`, `--hot-seats`,
`--hot-share`, `--retry-share`, `--users`).

Declines come back as `409 {"reason": ..., "message": ...}`, with the same
reason names as the `reservation_conflicts_total` metric: `seats_unavailable`,
`user_limit_exceeded`, `idempotency_key_reused`, `contention`. An idempotent
replay is the original `201` response plus an `Idempotent-Replayed: true`
header.

## Notes

- **Credentials.** `DB_*` values are read from `.env` (git-ignored) through
  python-dotenv; `.env.example` is the committed template.
- **MySQL version.** Django 6.0 requires MySQL >= 8.0.11 (MariaDB >= 10.6).
  Django 6.1 raised that floor to MySQL 8.4, which is why this project is
  pinned to Django 6.0.7 so it runs against a MySQL 8.0 server.
- **Keep `.env` values unquoted.** python-dotenv strips surrounding quotes,
  but Compose's env-file parser is stricter, so `DB_PASSWORD='secret'` can
  reach the `db` container with the quotes still in the value. Confirm what
  Compose resolves with `docker compose config`.
- **MySQL driver.** Django's MySQL backend imports `MySQLdb`. We use PyMySQL, a
  pure-Python drop-in replacement, registered in `bookmyshow/__init__.py` — so
  no C compiler or `libmysqlclient` is required at deploy time.
- **Custom user model.** `AUTH_USER_MODEL = 'accounts.Tbl_Users'` must be set
  *before* the project's first migration. Once migrations exist, changing it
  makes Django refuse to touch an existing database with
  `InconsistentMigrationHistory: Migration admin.0001_initial is applied before
  its dependency accounts.0001_initial`. Reset the database instead: drop and
  recreate it locally, or run `docker compose down -v` for the container, then
  `migrate`.
- **Troubleshooting.** `manage.py check`, `migrate` and `runserver` all open a
  MySQL connection. `OperationalError: (1045, "Access denied ...")` means the
  `DB_*` values above do not match your MySQL server.
