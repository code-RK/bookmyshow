"""Custom Prometheus metrics, exposed on ``GET /metrics`` (docs/api-contract.md).

django-prometheus already exports its own ``django_*`` request and database
metrics; these add the ones the API contract names, plus business metrics for
the reservation flow and per-show seat counts read straight from the database.

gunicorn runs several worker processes, each with its own copy of every
counter. With ``PROMETHEUS_MULTIPROC_DIR`` set (see the Dockerfile), each
worker writes its values to files in that directory and ``metrics_view``
adds them up, so /metrics reports the whole service rather than whichever
worker answered the scrape.
"""

import logging
import os

from django.db.models import Count
from django.http import HttpResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    disable_created_metrics,
    generate_latest,
    multiprocess,
)
from prometheus_client.core import GaugeMetricFamily

logger = logging.getLogger('api')

# Drop the ``*_created`` timestamp series that every counter and histogram
# exports by default. Prometheus does not need them, and they roughly double
# the size of /metrics. Applies to django-prometheus' metrics too.
disable_created_metrics()


# --- HTTP ------------------------------------------------------------------

# ``path`` is the URL route template (e.g. ``/api/v1/shows/<int:show_id>``),
# not the raw path: one label value per show/reservation id would grow the
# number of time series without bound.
HTTP_REQUESTS = Counter(
    'http_requests',
    'Total number of HTTP requests',
    ['method', 'path', 'status'],
)

HTTP_REQUEST_DURATION = Histogram(
    'http_request_duration_seconds',
    'HTTP request latency in seconds',
    ['method', 'path'],
)


# --- Reservations ----------------------------------------------------------

RESERVATION_ATTEMPTS = Counter(
    'reservation_attempts',
    'Total number of reservation attempts',
)

RESERVATION_SUCCESS = Counter(
    'reservation_success',
    'Total successful reservations (idempotent replays not counted)',
)

RESERVATION_CONFLICTS = Counter(
    'reservation_conflicts',
    'Reservation requests that booked nothing new, by reason '
    '(409 declines, plus idempotent replays of an earlier booking)',
    ['reason'],
)

RESERVATION_CANCELLATIONS = Counter(
    'reservation_cancellations',
    'Total cancelled reservations',
)

RESERVATION_LATENCY = Histogram(
    'reservation_latency_seconds',
    'Time spent processing reservation requests',
)

CONFLICT_REASONS = (
    'seats_unavailable',       # 409: a requested seat is already confirmed
    'user_limit_exceeded',     # 409: over the show's per_user_limit
    'idempotency_key_reused',  # 409: same key, different request
    'idempotent_replay',       # 201: same key, same request -> original response
    'contention',              # 409: still deadlocking after every retry
)

# Create every reason up front so each one is exported as 0 from startup,
# instead of being missing until its first occurrence.
for reason in CONFLICT_REASONS:
    RESERVATION_CONFLICTS.labels(reason)


# --- Seat inventory (read from the database at scrape time) ----------------

class SeatInventoryCollector:
    """Per-show seat counts, computed from ``tbl_seat`` on every scrape.

    A gauge kept in memory would drift from the database (and differs per
    worker); counting rows at scrape time always matches GET /shows/{id}.
    """

    STATUSES = ('available', 'held', 'confirmed')

    def collect(self):
        # Imported here: this module is imported before the apps are ready.
        from show.models import Tbl_Seat

        gauges = {
            status: GaugeMetricFamily(
                f'seats_{status}', f'Seats currently {status}, per show', labels=['show_id'],
            )
            for status in self.STATUSES
        }
        total = GaugeMetricFamily('seats_total', 'Seats per show', labels=['show_id'])

        try:
            rows = (
                Tbl_Seat.objects
                .order_by()
                .values_list('show_id', 'status')
                .annotate(n=Count('id'))
            )
            per_show = {}
            for show_id, status, n in rows:
                per_show.setdefault(show_id, dict.fromkeys(self.STATUSES, 0))[status] = n
        except Exception:
            # A DB outage must not turn the scrape into a 500; the counters
            # are still worth having. /health/ready reports the outage.
            logger.exception('seat inventory metrics unavailable')
            return

        for show_id, counts in sorted(per_show.items()):
            for status, n in counts.items():
                gauges[status].add_metric([str(show_id)], n)
            total.add_metric([str(show_id)], sum(counts.values()))

        yield from gauges.values()
        yield total


SEAT_REGISTRY = CollectorRegistry()
SEAT_REGISTRY.register(SeatInventoryCollector())


def metrics_view(request):
    """``GET /metrics`` in the Prometheus text format."""
    if 'PROMETHEUS_MULTIPROC_DIR' in os.environ:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        # Single process (e.g. `manage.py runserver`): the default registry.
        registry = REGISTRY
    body = generate_latest(registry) + generate_latest(SEAT_REGISTRY)
    return HttpResponse(body, content_type=CONTENT_TYPE_LATEST)
