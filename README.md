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
│   │   └── tbl_users.py   # Tbl_Users - the AUTH_USER_MODEL (table Tbl_Users)
│   ├── serializers.py     # RegisterSerializer / LoginSerializer
│   ├── views.py
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

# A protected endpoint (once implemented) takes the bearer token:
Invoke-RestMethod -Uri http://localhost:8000/api/v1/shows/1 `
    -Headers @{ Authorization = "Bearer $token" }
```

- Access tokens last **30 minutes**, refresh tokens **1 day** (`SIMPLE_JWT` in
  `settings.py`); SimpleJWT's own defaults are 5 minutes / 1 day.
- Passwords are hashed by `create_user()` and validated against Django's
  `AUTH_PASSWORD_VALIDATORS` during registration.
- Duplicate `username` → `400`; wrong credentials → `401`.
- Registration and login run against the project's own user model,
  `accounts.models.Tbl_Users` (table `Tbl_Users`), declared as
  `AUTH_USER_MODEL` in `settings.py`. It subclasses `AbstractUser` and adds the
  `role` column described in `docs/db-architecture.md`.
- `role` defaults to `customer` and is **not** writable through the register
  endpoint — otherwise anyone could register themselves as an admin. Promote a
  user to `admin` from `/admin/` (`accounts/models/tbl_users.py`).

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
  `manage.py migrate --noinput`, then `exec`s gunicorn on `0.0.0.0:8000`
  (`bookmyshow.wsgi:application`).
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
