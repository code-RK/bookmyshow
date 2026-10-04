import logging
import re
import time
import uuid

from .log import bound_fields, end_request, start_request
from .metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS

request_logger = logging.getLogger('api.request')

# A caller-supplied X-Request-ID is reused only if it is short and plain, so it
# cannot inject odd characters into logs or headers.
_VALID_REQUEST_ID = re.compile(r'^[A-Za-z0-9._-]{1,64}$')

# Polled constantly (Prometheus every 15s, platform health checks); logged only
# when they fail, so they do not drown out real traffic.
_QUIET_PREFIXES = ('/metrics', '/health/')


class RequestLogMiddleware:
    """Assigns the request id and writes one JSON line per request.

    Outermost middleware, so the id is in place for everything below it and the
    duration covers the whole request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get('X-Request-ID', '')
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        tokens = start_request(request_id)
        start = time.perf_counter()
        try:
            # Django turns exceptions raised below into 500 responses (and
            # logs them on django.request), so a response always comes back.
            response = self.get_response(request)
            response['X-Request-ID'] = request_id

            status = response.status_code
            if status < 400 and request.path.startswith(_QUIET_PREFIXES):
                return response

            match = getattr(request, 'resolver_match', None)
            # DRF stores the token's user on the underlying request too.
            user = getattr(request, 'user', None)
            entry = {
                'method': request.method,
                'path': request.path,
                'route': f'/{match.route}' if match and match.route else None,
                'status': status,
                'duration_ms': round((time.perf_counter() - start) * 1000, 1),
                'user_id': user.pk if user is not None and user.is_authenticated else None,
            }
            entry.update(bound_fields())
            # For a rejected request, say why (DRF responses carry the body as data).
            data = getattr(response, 'data', None)
            if status >= 400 and isinstance(data, dict):
                entry.setdefault('reason', data.get('reason'))
                # Field validation errors (e.g. {"username": [...]}) have no
                # message/detail key; log the field errors themselves.
                entry['error'] = data.get('message') or data.get('detail') or data
            entry = {k: v for k, v in entry.items() if v is not None}

            level = logging.ERROR if status >= 500 else logging.INFO
            request_logger.log(level, f"{request.method} {request.path} {status}", extra=entry)
            return response
        finally:
            end_request(tokens)


class RequestMetricsMiddleware:
    """Records ``http_requests_total`` and ``http_request_duration_seconds``."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.perf_counter()
        response = self.get_response(request)
        duration = time.perf_counter() - start

        # resolver_match is set once URL resolution has run; a request that
        # matched no route (404) is grouped under one label value so random
        # paths cannot create new series.
        match = getattr(request, 'resolver_match', None)
        path = f'/{match.route}' if match and match.route else 'unmatched'

        HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        HTTP_REQUEST_DURATION.labels(request.method, path).observe(duration)
        return response
