import time

from .metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS


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
