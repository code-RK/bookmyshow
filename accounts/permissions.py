"""Reusable DRF permission classes for this project."""

from rest_framework.permissions import BasePermission

from .models import Tbl_Users


class IsAdminRole(BasePermission):
    """Allow only authenticated users whose ``Tbl_Users.role`` is ``admin``.

    DRF's built-in ``IsAdminUser`` checks ``is_staff``, but this project
    expresses "admin" through the ``role`` column described in
    ``docs/db-architecture.md``, so that is the attribute checked here.

    Combine with ``IsAuthenticated`` so an anonymous caller is rejected with
    401 before this class is reached; without it an anonymous request would
    fall through to a 403.
    """

    message = 'Admin privileges required.'

    def has_permission(self, request, view):
        user = request.user
        # is_authenticated is checked first: AnonymousUser has no `role`.
        return bool(
            user
            and user.is_authenticated
            and user.role == Tbl_Users.Role.ADMIN
        )
