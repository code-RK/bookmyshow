from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Tbl_Users


@admin.register(Tbl_Users)
class TblUsersAdmin(UserAdmin):
    """Expose Tbl_Users in the Django admin.

    Extending UserAdmin keeps the password-hashing widgets and the
    permissions/group editors working.
    """

    fieldsets = UserAdmin.fieldsets + (
        ('BookMyShow', {'fields': ('role',)}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('BookMyShow', {'fields': ('role',)}),
    )
    list_display = ('username', 'email', 'role', 'is_staff', 'is_active')
    list_filter = UserAdmin.list_filter + ('role',)

