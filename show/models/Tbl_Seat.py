"""Seat table - one bookable seat that belongs to exactly one show.

See ``docs/db-architecture.md`` -> ``Seat``.
"""

from django.db import models

from .Tbl_Show import Tbl_Show
from .Tbl_Reservation import Tbl_Reservation


class Tbl_Seat(models.Model):
    """A seat inside a show."""

    class Status(models.TextChoices):
        AVAILABLE = 'available'
        # A reservation confirms its seats immediately; there is no separate
        # "held" phase (seats are released by cancelling the reservation).
        CONFIRMED = 'confirmed'

    show = models.ForeignKey(Tbl_Show, on_delete=models.CASCADE, related_name='seats')
    seat_number = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    # SET_NULL, not CASCADE: a reservation must be removable without losing the
    # seat row. Referenced by name because Tbl_Reservation is imported later
    # by show.models.__init__.
    current_reservation = models.ForeignKey(Tbl_Reservation, on_delete=models.SET_NULL, null=True, blank=True, related_name='seats_held')

    class Meta:
        db_table = 'tbl_seat'
        # Keep insertion order so a GET echoes the seat order from the POST.
        ordering = ('id',)
        constraints = [
            models.UniqueConstraint(
                fields=('show', 'seat_number'),
                name='uniq_seat_per_show',
            ),
        ]

    def __str__(self):
        return f'{self.show_id}: {self.seat_number}'
