"""User table for the accounts app."""

from django.contrib.auth.models import AbstractUser
from django.db import models


class Tbl_Users(AbstractUser):
    """Application user, stored in the ``Tbl_Users`` table.

    Subclasses ``AbstractUser`` so that password hashing, ``createsuperuser``,
    permissions and SimpleJWT keep working out of the box, while the table name
    matches this project's naming convention.
    """

    class Role(models.TextChoices):
        ADMIN = 'admin'
        CUSTOMER = 'customer'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)

    class Meta:
        db_table = 'tbl_users'
        verbose_name = 'user'
        verbose_name_plural = 'users'

    def __str__(self):
        return self.username
