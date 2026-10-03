from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from django.db import connection


class LivenessView(APIView):
    permission_classes = []

    def get(self, request):
        return Response(
            {"status": "ok"},
            status=status.HTTP_200_OK
        )

class ReadinessView(APIView):
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
            return Response(
                {
                    "status": "not_ready",
                    "database": "unavailable"
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )