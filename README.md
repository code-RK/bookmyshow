# bookmyshow
Scalable backend service to enable booking of a show and survives stampede requests.

## Tech stack

| Component | Choice |
| --- | --- |
| Language | Python 3.14 |
| Framework | Django 6.1 |
| API layer | Django REST Framework + SimpleJWT |
| Database | MySQL (accessed through PyMySQL) |

## Project layout

```
bookmyshow/                # repository root
├── manage.py
├── requirements.txt
├── bookmyshow/            # Django project package
│   ├── __init__.py        # registers PyMySQL as MySQLdb
│   ├── settings.py        # static DB credentials live here for now
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
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

3. Set the credentials in `bookmyshow/settings.py` (currently hard-coded):

   ```python
   DB_NAME = 'bookmyshow'
   DB_USER = 'root'
   DB_PASSWORD = '<your-mysql-password>'   # static value, set in settings.py
   DB_HOST = '127.0.0.1'
   DB_PORT = '3306'
   ```

4. Apply migrations and start the dev server:

   ```powershell
   python manage.py migrate
   python manage.py runserver
   ```

## Notes

- **Static credentials.** `DB_PASSWORD` and friends are hard-coded for now;
  they will move to environment variables / a secrets manager later.
- **MySQL driver.** Django's MySQL backend imports `MySQLdb`. We use PyMySQL, a
  pure-Python drop-in replacement, registered in `bookmyshow/__init__.py` — so
  no C compiler or `libmysqlclient` is required at deploy time.
- **Troubleshooting.** `manage.py check`, `migrate` and `runserver` all open a
  MySQL connection. `OperationalError: (1045, "Access denied ...")` means the
  `DB_*` values above do not match your MySQL server.
