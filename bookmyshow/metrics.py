"""Custom Prometheus metrics, exposed on ``GET /metrics`` (docs/api-contract.md).

django-prometheus already exports its own ``django_*`` request, database and
migration metrics; these add the ones the API contract names, plus business
metrics for the reservation flow.
"""

from prometheus_client import Counter, Histogram, disable_created_metrics

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
    'Total reservation conflicts (409), by reason',
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

# Create every reason up front so each one is exported as 0 from startup,
# instead of being missing until its first conflict.
for reason in ('seats_unavailable', 'user_limit_exceeded', 'idempotency_key_reused'):
    RESERVATION_CONFLICTS.labels(reason)
