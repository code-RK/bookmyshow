import logging

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from django.db import connection

logger = logging.getLogger('api.health')


class LivenessView(APIView):
    """``GET /health/live``: the process is up and serving requests."""

    # Public, and never authenticated: a health checker sending a stray or
    # expired Authorization header must not get a 401.
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response(
            {"status": "ok"},
            status=status.HTTP_200_OK
        )

class ReadinessView(APIView):
    """``GET /health/ready``: 200 only if the database answers, else 503."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        try:
            # A real round trip: ensure_connection() returns early when a
            # persistent connection (CONN_MAX_AGE) is already open, even if
            # the database has since gone away.
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")

            return Response(
                {
                    "status": "ready",
                    "database": "ok"
                },
                status=status.HTTP_200_OK
            )

        except Exception:
            # Fail closed, and record why (connection refused, access denied,
            # unknown host...) - the 503 alone does not say.
            logger.exception("readiness check failed: database unavailable")
            return Response(
                {
                    "status": "not_ready",
                    "database": "unavailable"
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
