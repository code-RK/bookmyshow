"""
bookmyshow project package.

Django's MySQL backend (``django.db.backends.mysql``) looks for the native
``MySQLdb`` driver. PyMySQL is a pure-Python drop-in replacement, so we register
it as ``MySQLdb`` here. This must run before Django loads the database backend,
which is why it lives in the project package ``__init__``.
"""

import pymysql

pymysql.install_as_MySQLdb()
