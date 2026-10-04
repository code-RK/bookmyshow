"""Join table linking a reservation to every seat it covers.

See ``docs/db-architecture.md`` -> ``ReservationSeat``.
"""

from django.db import models

from .Tbl_Reservation import Tbl_Reservation
from .Tbl_Seat import Tbl_Seat


class Tbl_Reservation_Seat(models.Model):
    """Many-to-many helper between reservations and seats."""

    reservation = models.ForeignKey(Tbl_Reservation, on_delete=models.CASCADE, related_name='reservation_seats',)
    seat = models.ForeignKey(Tbl_Seat, on_delete=models.CASCADE, related_name='reservation_seats')

    class Meta:
        db_table = 'tbl_reservation_seat'
        constraints = [
            models.UniqueConstraint(
                fields=('reservation', 'seat'),
                name='uniq_reservation_seat',
            ),
        ]

    def __str__(self):
        return f'{self.reservation_id}: {self.seat_id}'
