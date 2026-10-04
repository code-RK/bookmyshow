"""``manage.py ensure_admin``: create or update the admin account from env vars.

The register endpoint deliberately cannot create admins, so a fresh deploy has
no way to create a show. docker/entrypoint.sh runs this on every start when
``ADMIN_USERNAME`` and ``ADMIN_PASSWORD`` are set, making the environment the
source of truth for the admin login (the burst script uses the same pair).
"""

import os

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Tbl_Users


class Command(BaseCommand):
    help = 'Create or update the admin user named by ADMIN_USERNAME / ADMIN_PASSWORD.'

    def handle(self, *args, **options):
        username = os.environ.get('ADMIN_USERNAME', '').strip()
        password = os.environ.get('ADMIN_PASSWORD', '')
        if not username or not password:
            raise CommandError('ADMIN_USERNAME and ADMIN_PASSWORD must both be set.')

        user, created = Tbl_Users.objects.get_or_create(username=username)
        changed = []
        if user.role != Tbl_Users.Role.ADMIN:
            user.role = Tbl_Users.Role.ADMIN
            changed.append('role')
        # check_password costs one hash, but this runs once per start.
        if not user.check_password(password):
            user.set_password(password)
            changed.append('password')
        if created or changed:
            user.save()

        if created:
            self.stdout.write(f'Created admin user "{username}".')
        elif changed:
            self.stdout.write(f'Updated admin user "{username}" ({", ".join(changed)}).')
        else:
            self.stdout.write(f'Admin user "{username}" is up to date.')
