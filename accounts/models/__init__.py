"""Model package for the accounts app.

Django imports ``accounts.models`` when the app registry loads, so every model
has to be re-exported here to be registered (and therefore picked up by
``makemigrations``).
"""

from .Tbl_Users import *

__all__ = ['Tbl_Users']
