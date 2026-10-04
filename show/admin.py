from django.contrib import admin

from .models import Tbl_Reservation, Tbl_Reservation_Seat, Tbl_Seat, Tbl_Show


@admin.register(Tbl_Show)
class TblShowAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'price_paise', 'per_user_limit')
    search_fields = ('name',)


@admin.register(Tbl_Seat)
class TblSeatAdmin(admin.ModelAdmin):
    list_display = ('id', 'show', 'seat_number', 'status', 'current_reservation')
    list_filter = ('status',)


@admin.register(Tbl_Reservation)
class TblReservationAdmin(admin.ModelAdmin):
    list_display = ('id', 'show', 'user', 'amount_paise', 'status', 'created_at')
    list_filter = ('status',)


admin.site.register(Tbl_Reservation_Seat)
