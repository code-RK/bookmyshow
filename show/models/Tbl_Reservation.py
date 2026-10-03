"""Reservation table - a user's hold/booking over one or more seats.

See ``docs/db-architecture.md`` -> ``Reservation``.
"""

from django.conf import settings
from django.db import models

from .Tbl_Show import Tbl_Show
from accounts.models.Tbl_Users import Tbl_Users


class Tbl_Reservation(models.Model):
    """A reservation.

    The reserve/cancel endpoints are not implemented yet; the table is created
    now so ``Tbl_Seat.current_reservation`` and the seat join table resolve.
    """

    class Status(models.TextChoices):
        CONFIRMED = 'confirmed'
        CANCELLED = 'cancelled'

    show = models.ForeignKey(Tbl_Show, on_delete=models.CASCADE, related_name='reservations')
    user = models.ForeignKey(Tbl_Users, on_delete=models.CASCADE, related_name='reservations')
    amount_paise = models.PositiveIntegerField()
    status = models.CharField( max_length=20, choices=Status.choices, default=Status.CONFIRMED,)
    created_at = models.DateTimeField(auto_now_add=True)
    # Null until the reservation is cancelled.
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'tbl_reservation'
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.pk} ({self.status})'
