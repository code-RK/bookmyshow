"""Show table - one bookable screening/event.

See ``docs/db-architecture.md`` -> ``Show``.
"""

from django.db import models


class Tbl_Show(models.Model):
    """A show.

    Seats are stored separately in :class:`Tbl_Seat`: creating a show inserts
    the show row plus one seat row per seat number in the request body.
    """

    name = models.CharField(max_length=200)
    price_paise = models.PositiveIntegerField()
    # Not accepted by POST /api/v1/shows yet (docs/api-contract.md), so it
    # needs a default. Tune it per show type once the booking flow lands.
    per_user_limit = models.PositiveIntegerField(default=4)

    class Meta:
        db_table = 'tbl_show'
        # id order == the order the seats were supplied in the request body.
        ordering = ('id',)

    def __str__(self):
        return self.name
